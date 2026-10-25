"""The shared embedding service: one copy of the embedding model
(services/embedding_service.py MODEL_NAME, with PyTorch, ~0.5 GB) for every
web worker, instead of one copy per worker.

    POST /embed   {"texts": ["...", ...]}  ->  {"vectors": [[...], ...]}
    GET  /health  ->  {"ok": true}

Runs as its own container (deploy/docker-compose.prod.yml "embedder"); the
web and scheduler containers reach it at EMBEDDING_URL=http://embedder:8001.
Not published outside the Docker network. When EMBEDDING_TOKEN is set, calls
must carry it in X-Embedding-Token.
"""

import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request  # noqa: E402

MAX_TEXTS = 64
MAX_CHARS = 8000

app = Flask("avir-embedder")
_model = None


def model():
    global _model  # pylint: disable=global-statement
    if _model is None:
        from sentence_transformers import SentenceTransformer

        from services.embedding_service import MODEL_NAME

        _model = SentenceTransformer(MODEL_NAME)
    return _model


@app.post("/embed")
def embed():
    token = os.getenv("EMBEDDING_TOKEN", "")
    if token and not hmac.compare_digest(request.headers.get("X-Embedding-Token", ""), token):
        return jsonify({"error": "forbidden"}), 403
    texts = (request.get_json(silent=True) or {}).get("texts")
    if not isinstance(texts, list) or not texts or len(texts) > MAX_TEXTS:
        return jsonify({"error": f"send 1-{MAX_TEXTS} texts"}), 400
    texts = [str(t)[:MAX_CHARS] for t in texts]
    return jsonify({"vectors": model().encode(texts).tolist()})


@app.get("/health")
def health():
    return jsonify({"ok": True})


# In the container (deploy/docker-compose.prod.yml), one gunicorn worker holds
# the one copy of the model:
#   gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8001 --chdir scripts embedding_server:app
if __name__ == "__main__":  # local try-out: python scripts/embedding_server.py
    model()
    app.run(host="127.0.0.1", port=int(os.getenv("EMBEDDING_PORT", "8001")))
