try:
    import chromadb
    from chromadb.config import Settings

    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

import numpy as np

from config import Config


class EmbeddingService:
    """Local semantic similarity for competitor posts, backed by the same
    ChromaDB local embedding model MemoryService already uses. Runs entirely
    offline (no external LLM/API call), so it works identically regardless of
    USE_MOCK_LLM."""

    def __init__(self):
        if not HAS_CHROMA:
            print("[EmbeddingService] chromadb not installed. Post clustering disabled.")
            self.enabled = False
            return
        try:
            self.client = chromadb.PersistentClient(
                path=Config.CHROMA_PERSIST_DIR, settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.client.get_or_create_collection(
                name="competitor_post_embeddings", metadata={"hnsw:space": "cosine"}
            )
            self.enabled = True
        except Exception as e:
            print(f"[EmbeddingService] ChromaDB initialization warning: {e}")
            self.enabled = False

    def index_posts(self, posts: list) -> None:
        """Upsert posts into the embedding store, keyed by post_url."""
        if not self.enabled or not posts:
            return

        ids, docs, metas = [], [], []
        for p in posts:
            url = p.get("post_url")
            text = f"{p.get('title') or ''}\n{(p.get('text') or '')[:500]}".strip()
            if not url or not text:
                continue
            ids.append(url)
            docs.append(text)
            metas.append(
                {
                    "competitor": p.get("_source_competitor") or p.get("competitor") or "",
                    "platform": p.get("platform") or "",
                }
            )

        if not ids:
            return
        try:
            self.collection.upsert(ids=ids, documents=docs, metadatas=metas)
        except Exception as e:
            print(f"[EmbeddingService] index_posts warning: {e}")

    def cluster_posts(
        self,
        posts: list,
        similarity_threshold: float = 0.78,
        min_cluster_size: int = 2,
        near_duplicate_threshold: float = 0.94,
    ) -> list:
        """Group posts by semantic similarity (connected components over a
        cosine-similarity threshold graph). Returns a list of clusters, each a
        list of the original post dicts. No competitor/platform gating -
        clusters can span any combination of the two.

        Near-duplicate posts (the same underlying news item republished
        near-verbatim across multiple RSS/aggregator sources - very common
        for wire-service stories) are collapsed to a single representative
        before clustering, at a much tighter threshold than the "same theme"
        one above. Without this, 4-5 near-identical headlines about one event
        would inflate a single cluster's apparent size/breadth and crowd out
        genuine post diversity in what CollectionAgent sees when labeling it -
        the actual root cause of "Suggested Storylines" feeling repetitive:
        the same wire story kept resurfacing as its own "storyline" every time
        a new outlet republished it within the 15-day window.
        """
        candidates = [p for p in posts if p.get("post_url")]
        if not self.enabled or len(candidates) < min_cluster_size:
            return []

        url_to_post = {p["post_url"]: p for p in candidates}
        urls = list(url_to_post.keys())

        # Posts are normally embedded at scan-save time (db.save_competitor_posts),
        # so only index whatever's still missing here - keeps repeated "Suggest
        # Storylines" runs fast instead of re-embedding the whole filtered set
        # every time. Backfills any posts saved before that hook existed.
        try:
            already_indexed = set(self.collection.get(ids=urls, include=[]).get("ids") or [])
        except Exception as e:
            print(f"[EmbeddingService] cluster_posts existence check warning: {e}")
            already_indexed = set()

        missing = [url_to_post[u] for u in urls if u not in already_indexed]
        if missing:
            self.index_posts(missing)

        try:
            result = self.collection.get(ids=urls, include=["embeddings"])
        except Exception as e:
            print(f"[EmbeddingService] cluster_posts fetch warning: {e}")
            return []

        got_ids = result.get("ids") or []
        embeddings = result.get("embeddings")
        if embeddings is None or len(got_ids) < min_cluster_size:
            return []

        vectors = np.array(embeddings, dtype=float)
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

        # Pass 1 (tight threshold): collapse near-duplicate/republished posts
        # to one representative each before thematic clustering, so a wire
        # story picked up by several aggregators doesn't count as several
        # distinct posts.
        dup_find, dup_union = make_union_find(n)
        for i in range(n):
            for j in range(i + 1, n):
                if sim_matrix[i, j] >= near_duplicate_threshold:
                    dup_union(i, j)

        dup_groups: dict[int, list[int]] = {}
        for i in range(n):
            dup_groups.setdefault(dup_find(i), []).append(i)

        # One representative index per near-duplicate group - prefer whichever
        # post has the longest text (most complete/informative version).
        representatives = [
            max(idxs, key=lambda i: len(url_to_post[got_ids[i]].get("text") or ""))
            for idxs in dup_groups.values()
        ]

        # Pass 2 (theme threshold): cluster the deduplicated representatives.
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
            # Expand each surviving representative back to every post in its
            # near-duplicate group, so the cluster still reflects the true
            # post_count/competitor breadth for ranking and display.
            expanded = [i for a in idxs for i in dup_groups[dup_find(representatives[a])]]
            if len(expanded) < min_cluster_size:
                continue
            cluster_posts = [url_to_post[got_ids[i]] for i in expanded if got_ids[i] in url_to_post]
            if len(cluster_posts) >= min_cluster_size:
                clusters.append(cluster_posts)

        # Rank cross-competitor / cross-platform clusters higher, then by size.
        def breadth_score(cluster):
            competitors = {p.get("_source_competitor") or p.get("competitor") for p in cluster}
            platforms = {p.get("platform") for p in cluster}
            return (len(competitors), len(platforms), len(cluster))

        clusters.sort(key=breadth_score, reverse=True)
        return clusters
