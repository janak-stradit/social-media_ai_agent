from services.llm_service import LLMService


class CollectionAgent:
    """Agent that labels clusters of similar competitor posts (produced by
    EmbeddingService.cluster_posts) with a short theme, description, and
    relevance to our own project context - one LLM call for every cluster at
    once, so cost stays bounded regardless of how many posts were scanned."""

    def __init__(self):
        self.llm = LLMService()

    def label_clusters(self, clusters: list, project_context: str) -> list:
        """clusters: list of lists of post dicts (already grouped by similarity).
        Returns clusters annotated with label/description/relevance, in the
        same order as the input."""
        if not clusters:
            return []

        system = """You are an expert content strategist reviewing groups of competitor social/content posts.
Each group already contains posts that are semantically similar (likely the same underlying story or theme, \
sometimes from multiple competitors).

For each group, provide:
- "label": a short punchy theme name (under 8 words), e.g. "ESG & Sustainable Investing Push"
- "description": 1-2 sentences summarizing the shared story/theme and why it's worth a counter-strategy or storyline
- "relevance": "high", "medium", or "low" - how relevant this theme is to OUR company's projects/capabilities

Return ONLY a valid JSON object with this schema:
{"clusters": [{"cluster_index": 0, "label": "...", "description": "...", "relevance": "high"}, ...]}
One entry per group, in the same order given, using the exact cluster_index shown."""

        clusters_text = ""
        for idx, cluster in enumerate(clusters):
            clusters_text += f"\n=== GROUP {idx} ({len(cluster)} posts) ===\n"
            for p in cluster[:4]:  # cap sample size per cluster to keep the prompt bounded
                comp = p.get("_source_competitor") or p.get("competitor") or "Unknown"
                text = (p.get("text") or p.get("title") or "")[:300]
                clusters_text += f"[{comp} / {p.get('platform', '')}] {text}\n---\n"

        user = (
            f"<OUR_PROJECT_CONTEXT>\n{project_context}\n</OUR_PROJECT_CONTEXT>\n\n"
            f"<POST_GROUPS>\n{clusters_text}\n</POST_GROUPS>\n\nLabel each group."
        )

        try:
            result = self.llm.generate_json(system, user, temperature=0.2)
            labels_by_index = {}
            for item in result.get("clusters", []):
                try:
                    labels_by_index[int(item.get("cluster_index"))] = item
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            print(f"[CollectionAgent] Error labeling clusters: {e}")
            labels_by_index = {}

        annotated = []
        for idx, cluster in enumerate(clusters):
            meta = labels_by_index.get(idx, {})
            annotated.append(
                {
                    "label": meta.get("label") or f"Related Storyline ({len(cluster)} posts)",
                    "description": meta.get("description") or "Multiple competitor posts on a related theme.",
                    "relevance": meta.get("relevance") or "medium",
                    "posts": cluster,
                }
            )
        return annotated
