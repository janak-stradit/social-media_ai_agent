"""Embeddings come from one shared service when EMBEDDING_URL is set
(scripts/embedding_server.py), otherwise from a model loaded once per process
(services/embedding_service.py). The model and the network are faked."""

import importlib
import sys

import requests

from config import Config
from services import embedding_service as es


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_the_shared_service_is_used_when_set(monkeypatch):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return Resp(200, {"vectors": [[0.1, 0.2]]})

    monkeypatch.setattr(Config, "EMBEDDING_URL", "http://embedder:8001")
    monkeypatch.setattr(Config, "EMBEDDING_TOKEN", "secret")
    monkeypatch.setattr(es.requests, "post", post)
    monkeypatch.setattr(es, "_local_model", lambda: (_ for _ in ()).throw(AssertionError("no local model")))
    assert es.EmbeddingService().get_embedding("hello") == [0.1, 0.2]
    assert calls == [("http://embedder:8001/embed", {"texts": ["hello"]}, {"X-Embedding-Token": "secret"})]


def test_an_unreachable_service_means_no_memory_not_an_error(monkeypatch):
    def down(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(Config, "EMBEDDING_URL", "http://embedder:8001")
    monkeypatch.setattr(es.requests, "post", down)
    assert es.EmbeddingService().get_embedding("hello") == []


def test_the_local_model_loads_once(monkeypatch):
    loads = []

    class FakeModel:
        def __init__(self, name):
            loads.append(name)

        def encode(self, texts):
            import numpy as np

            return np.array([[1.0, 0.0] for _ in texts])

    fake_module = type(sys)("sentence_transformers")
    fake_module.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(Config, "EMBEDDING_URL", "")
    monkeypatch.setattr(es, "_model", None)
    monkeypatch.setattr(es, "_model_failed", False)
    for _ in range(3):
        assert es.EmbeddingService().get_embedding("x") == [1.0, 0.0]
    assert loads == [es.MODEL_NAME]


def test_the_embedding_server_checks_its_token_and_input(monkeypatch):
    import numpy as np

    sys.path.insert(0, "scripts")
    server = importlib.import_module("embedding_server")

    class FakeModel:
        def encode(self, texts):
            return np.array([[0.5] for _ in texts])

    monkeypatch.setattr(server, "_model", FakeModel())
    monkeypatch.setenv("EMBEDDING_TOKEN", "t0k")
    client = server.app.test_client()
    assert client.post("/embed", json={"texts": ["a"]}).status_code == 403
    ok = client.post("/embed", json={"texts": ["a", "b"]}, headers={"X-Embedding-Token": "t0k"})
    assert ok.get_json() == {"vectors": [[0.5], [0.5]]}
    too_many = client.post("/embed", json={"texts": ["a"] * 65}, headers={"X-Embedding-Token": "t0k"})
    assert too_many.status_code == 400 and client.get("/health").get_json() == {"ok": True}


def test_web_workers_no_longer_import_torch_for_vision():
    import services.hf_service as hf

    assert "from transformers import pipeline\n" not in open(hf.__file__).read().split("class HuggingFaceService")[0]
