from services.llm_service import LLMService


class CollectionAgent:
    """Agent that labels clusters of similar competitor posts (produced by
    EmbeddingService.cluster_posts) with a short theme, description, and
    relevance to our own project context - one LLM call for every cluster at
    once, so cost stays bounded regardless of how many posts were scanned."""

    def __init__(self):
        self.llm = LLMService()

    @staticmethod
    def _diverse_sample(cluster: list, n: int) -> list:
        """Picks up to n posts favoring distinct competitor/platform
        combinations first, instead of just cluster[:n] - a cluster's first
        few posts are often near-identical republishes of the same wire story
        (same competitor, same platform, back-to-back in scrape order), which
        gave the LLM a repetitive, low-signal sample to label from instead of
        seeing the actual breadth of the story."""
        seen_keys = set()
        diverse, rest = [], []
        for p in cluster:
            key = (p.get("_source_competitor") or p.get("competitor"), p.get("platform"))
            if key not in seen_keys:
                seen_keys.add(key)
                diverse.append(p)
            else:
                rest.append(p)
        return (diverse + rest)[:n]

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
- "relevance": "high", "medium", or "No Strong Match". YOU MUST strictly evaluate alignment against <OUR_PROJECT_CONTEXT>. If the theme does NOT directly map to a specific project, capability, or strategic goal explicitly mentioned in OUR_PROJECT_CONTEXT, you MUST output "No Strong Match". Do not output "medium" for generic industry topics unless we have a specific capability to address it.

Return ONLY a valid JSON object with this schema:
{"clusters": [{"cluster_index": 0, "label": "...", "description": "...", "relevance": "high"}, ...]}
One entry per group, in the same order given, using the exact cluster_index shown."""

        clusters_text = ""
        for idx, cluster in enumerate(clusters):
            clusters_text += f"\n=== GROUP {idx} ({len(cluster)} posts) ===\n"
            for p in self._diverse_sample(cluster, 4):
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
            meta = labels_by_index.get(idx)
            if not meta:
                continue

            relevance = str(meta.get("relevance", "")).lower()
            label = str(meta.get("label", "")).lower()
            
            # Filter out clusters that do not align with any StradIT project
            if "no strong match" in relevance or relevance in ["low", "none", "", "n/a"]:
                continue
                
            annotated.append(
                {
                    "label": meta.get("label") or f"Related Storyline ({len(cluster)} posts)",
                    "description": meta.get("description") or "Multiple competitor posts on a related theme.",
                    "relevance": meta.get("relevance") or "medium",
                    "posts": cluster,
                }
            )
        return annotated
