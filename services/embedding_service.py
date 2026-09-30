import numpy as np
import json
from sentence_transformers import SentenceTransformer

from db import engine, Session, CompetitorPostEmbedding


class EmbeddingService:
    """Local semantic similarity for competitor posts and memory, backed by
    SentenceTransformers and PostgreSQL instead of ChromaDB. Runs entirely
    offline (no external LLM/API call)."""

    def __init__(self):
        self.enabled = True
        try:
            # Load the same model ChromaDB uses by default
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            print(f"[EmbeddingService] Initialization warning: {e}")
            self.enabled = False

    def get_embedding(self, text: str) -> list[float]:
        if not self.enabled or not text:
            return []
        try:
            vector = self.model.encode(text)
            return vector.tolist()
        except Exception as e:
            print(f"[EmbeddingService] Error generating embedding: {e}")
            return []

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled or not texts:
            return []
        try:
            vectors = self.model.encode(texts)
            return vectors.tolist()
        except Exception as e:
            print(f"[EmbeddingService] Error generating embeddings: {e}")
            return []

    def index_posts(self, posts: list) -> None:
        """Upsert posts into the PostgreSQL store, keyed by post_url."""
        if not self.enabled or not posts:
            return

        with Session(engine) as session:
            for p in posts:
                url = p.get("post_url")
                text = f"{p.get('title') or ''}\n{(p.get('text') or '')[:500]}".strip()
                if not url or not text:
                    continue
                
                meta = {
                    "competitor": p.get("_source_competitor") or p.get("competitor") or "",
                    "platform": p.get("platform") or "",
                }
                
                embedding_vector = self.get_embedding(text)
                if not embedding_vector:
                    continue
                    
                emb = CompetitorPostEmbedding(
                    id=url,
                    content=text,
                    metadata_json=json.dumps(meta),
                    embedding_array=json.dumps(embedding_vector)
                )
                session.merge(emb)
            try:
                session.commit()
            except Exception as e:
                print(f"[EmbeddingService] index_posts warning: {e}")

    def cluster_posts(
        self,
        posts: list,
        similarity_threshold: float = 0.78,
        min_cluster_size: int = 2,
        near_duplicate_threshold: float = 0.94,
    ) -> list:
        candidates = [p for p in posts if p.get("post_url")]
        if not self.enabled or len(candidates) < min_cluster_size:
            return []

        url_to_post = {p["post_url"]: p for p in candidates}
        urls = list(url_to_post.keys())

        # Check existing embeddings in Postgres
        with Session(engine) as session:
            existing_records = session.query(CompetitorPostEmbedding).filter(CompetitorPostEmbedding.id.in_(urls)).all()
            already_indexed = {r.id for r in existing_records}

        missing = [url_to_post[u] for u in urls if u not in already_indexed]
        if missing:
            self.index_posts(missing)

        # Re-fetch all embeddings for the requested URLs
        embeddings_map = {}
        with Session(engine) as session:
            final_records = session.query(CompetitorPostEmbedding).filter(CompetitorPostEmbedding.id.in_(urls)).all()
            for r in final_records:
                try:
                    embeddings_map[r.id] = json.loads(r.embedding_array)
                except Exception:
                    pass

        got_ids = []
        embeddings_list = []
        for url in urls:
            if url in embeddings_map:
                got_ids.append(url)
                embeddings_list.append(embeddings_map[url])

        if not embeddings_list or len(got_ids) < min_cluster_size:
            return []

        vectors = np.array(embeddings_list, dtype=float)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1e-9
        normalized = vectors / norms
        sim_matrix = normalized @ normalized.T

        n = len(got_ids)

        def make_union_find(size):
            parent = list(range(size))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

            return find, union

        dup_find, dup_union = make_union_find(n)
        for i in range(n):
            for j in range(i + 1, n):
                if sim_matrix[i, j] >= near_duplicate_threshold:
                    dup_union(i, j)

        dup_groups: dict[int, list[int]] = {}
        for i in range(n):
            dup_groups.setdefault(dup_find(i), []).append(i)

        representatives = [
            max(idxs, key=lambda i: len(url_to_post[got_ids[i]].get("text") or ""))
            for idxs in dup_groups.values()
        ]

        rep_count = len(representatives)
        find, union = make_union_find(rep_count)
        for a in range(rep_count):
            for b in range(a + 1, rep_count):
                if sim_matrix[representatives[a], representatives[b]] >= similarity_threshold:
                    union(a, b)

        groups: dict[int, list[int]] = {}
        for a in range(rep_count):
            groups.setdefault(find(a), []).append(a)

        clusters = []
        for idxs in groups.values():
            expanded = [i for a in idxs for i in dup_groups[dup_find(representatives[a])]]
            if len(expanded) < min_cluster_size:
                continue
            cluster_posts = [url_to_post[got_ids[i]] for i in expanded if got_ids[i] in url_to_post]
            if len(cluster_posts) >= min_cluster_size:
                clusters.append(cluster_posts)

        def breadth_score(cluster):
            competitors = {p.get("_source_competitor") or p.get("competitor") for p in cluster}
            platforms = {p.get("platform") for p in cluster}
            return (len(competitors), len(platforms), len(cluster))

        clusters.sort(key=breadth_score, reverse=True)
        return clusters
