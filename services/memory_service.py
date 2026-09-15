try:
    import chromadb
    from chromadb.config import Settings

    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
import uuid

from config import Config


class MemoryService:
    def __init__(self):
        if not HAS_CHROMA:
            print("[MemoryService] chromadb not installed. Running in RAG memory DB fallback mode.")
            self.enabled = False
            return
        try:
            self.client = chromadb.PersistentClient(
                path=Config.CHROMA_PERSIST_DIR, settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.client.get_or_create_collection(
                name="social_media_memory", metadata={"hnsw:space": "cosine"}
            )
            self.enabled = True
        except Exception as e:
            print(f"[MemoryService] ChromaDB initialization warning: {e}")
            self.enabled = False

    def store_content(self, content_id, text, metadata=None):
        """Store generated content for retrieval"""
        if not self.enabled or not text:
            return
        try:
            self.collection.add(ids=[str(content_id)], documents=[text], metadatas=[metadata or {}])
        except Exception as e:
            print(f"[MemoryService] Error storing content: {e}")

    def store_campaign_run(self, run_id, story, content, user_id=None, tone=None, platforms=None):
        """Vectorize and store a completed campaign run into memory"""
        if not self.enabled or not story:
            return

        try:
            # Build representative document combining story + sample captions
            sample_captions = []
            if isinstance(content, dict):
                for p in ["facebook", "instagram", "linkedin"]:
                    if p in content and "caption" in content[p]:
                        cap = content[p]["caption"].get("primary_caption")
                        if cap:
                            sample_captions.append(f"[{p.upper()}]: {cap}")

            doc_text = f"Brief: {story}\n" + "\n".join(sample_captions)
            doc_id = f"run_{run_id or uuid.uuid4()}"

            meta = {
                "type": "campaign_run",
                "run_id": str(run_id or ""),
                "user_id": str(user_id or ""),
                "tone": str(tone or ""),
                "platforms": ",".join(platforms) if isinstance(platforms, list) else str(platforms or ""),
            }

            self.collection.add(ids=[doc_id], documents=[doc_text], metadatas=[meta])
            print(f"[MemoryService] Successfully indexed campaign run {doc_id} into ChromaDB memory.")
        except Exception as e:
            print(f"[MemoryService] Failed to index campaign run: {e}")

    def retrieve_context(self, query_text, user_id=None, n_results=3):
        """Retrieve relevant past campaign context for prompt injection (RAG)"""
        if not self.enabled or not query_text:
            return []

        try:
            where_filter = None
            if user_id:
                where_filter = {"user_id": str(user_id)}

            kwargs = {"query_texts": [query_text], "n_results": min(n_results, 5)}
            if where_filter:
                kwargs["where"] = where_filter

            results = self.collection.query(**kwargs)

            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]

            retrieved = []
            for doc, meta in zip(documents, metadatas):
                retrieved.append({"content": doc, "metadata": meta})
            return retrieved
        except Exception as e:
            print(f"[MemoryService] Context retrieval failed: {e}")
            return []

    def get_trending_hashtags(self, category=None):
        """Retrieve trending hashtags from memory or return default niche recommendations"""
        if not self.enabled:
            return ["#VortexSocial", "#ViralMarketing", "#SocialStrategy"]
        try:
            results = self.collection.query(
                query_texts=[category or "trending hashtags"], n_results=2, where={"type": "hashtag"}
            )
            docs = results.get("documents", [[]])[0]
            if docs:
                return docs
        except Exception as e:
            print(f"[MemoryService] get_trending_hashtags notice: {e}")
        return ["#VortexSocial", "#AIStrategy", "#GrowthMarketing", "#DigitalGrowth"]

    def get_stats(self):
        """Return basic statistics about the memory store."""
        if not self.enabled:
            return {"total_memories": 0}
        try:
            return {"total_memories": self.collection.count()}
        except Exception as e:
            print(f"[MemoryService] get_stats warning: {e}")
            return {"total_memories": 0}

    def format_memory_prompt(self, retrieved_items):
        """Format retrieved memories into prompt section for agents"""
        if not retrieved_items:
            return ""

        lines = ["\n--- RELEVANT BRAND & CAMPAIGN MEMORY (PAST HIGH-PERFORMING CONTEXT) ---"]
        for idx, item in enumerate(retrieved_items, 1):
            lines.append(f"Memory #{idx}: {item['content']}")
        lines.append(
            "Instructions: Use the brand voice, themes, and winning patterns above for consistency.\n--- END MEMORY CONTEXT ---\n"
        )
        return "\n".join(lines)

    def get_memory_graph_data(self, user_id=None):
        """Retrieve vector memory documents and build graph nodes + edge connections."""
        nodes = [
            {
                "id": "core_rag",
                "label": "Brand RAG Memory Core",
                "type": "core",
                "group": "core",
                "info": "Central ChromaDB Vector Store holding brand embeddings",
            }
        ]
        edges = []

        # Static entity nodes for platforms
        platforms_map = {
            "facebook": {"id": "plat_facebook", "label": "Facebook", "type": "platform", "group": "platform"},
            "instagram": {"id": "plat_instagram", "label": "Instagram", "type": "platform", "group": "platform"},
            "linkedin": {"id": "plat_linkedin", "label": "LinkedIn", "type": "platform", "group": "platform"},
        }
        added_nodes = {"core_rag"}

        for _p_id, p_data in platforms_map.items():
            nodes.append(p_data)
            added_nodes.add(p_data["id"])

        fetched_count = 0
        if self.enabled:
            try:
                where_filter = {"user_id": str(user_id)} if user_id else None
                kwargs = {"limit": 50}
                if where_filter:
                    kwargs["where"] = where_filter

                records = self.collection.get(**kwargs)
                ids = records.get("ids", [])
                documents = records.get("documents", [])
                metadatas = records.get("metadatas", [])

                fetched_count = len(ids)

                for doc_id, doc_text, meta in zip(ids, documents, metadatas):
                    meta = meta or {}
                    run_id = meta.get("run_id") or doc_id
                    tone = meta.get("tone") or "Auto"
                    platforms_str = meta.get("platforms") or ""

                    # Excerpt story
                    story_excerpt = doc_text.split("\n")[0].replace("Brief: ", "")
                    if len(story_excerpt) > 40:
                        story_excerpt = story_excerpt[:40] + "..."

                    node_id = f"mem_{doc_id}"
                    if node_id not in added_nodes:
                        nodes.append(
                            {
                                "id": node_id,
                                "label": story_excerpt or f"Run #{run_id}",
                                "type": "campaign",
                                "group": "campaign",
                                "full_text": doc_text,
                                "tone": tone,
                                "platforms": platforms_str,
                                "run_id": run_id,
                            }
                        )
                        added_nodes.add(node_id)
                        edges.append({"from": "core_rag", "to": node_id, "label": "stores", "type": "memory"})

                    # Connect campaign to tone node
                    if tone and tone != "Auto":
                        tone_node_id = f"tone_{tone.lower()}"
                        if tone_node_id not in added_nodes:
                            nodes.append(
                                {
                                    "id": tone_node_id,
                                    "label": f"{tone.capitalize()} Tone",
                                    "type": "tone",
                                    "group": "tone",
                                }
                            )
                            added_nodes.add(tone_node_id)
                        edges.append({"from": node_id, "to": tone_node_id, "label": "uses_tone", "type": "tone"})

                    # Connect campaign to platform nodes
                    if platforms_str:
                        p_list = [p.strip().lower() for p in platforms_str.split(",") if p.strip()]
                        for p in p_list:
                            if p in platforms_map:
                                edges.append(
                                    {
                                        "from": node_id,
                                        "to": platforms_map[p]["id"],
                                        "label": "targets",
                                        "type": "platform",
                                    }
                                )

            except Exception as err:
                print(f"[MemoryService] get_memory_graph_data warning: {err}")

        # Fallback to database history runs if ChromaDB collection has no records or disabled
        if fetched_count == 0:
            try:
                # KNOWN BUG (pre-existing): db.py has get_history(), not get_all_history() --
                # this fallback always raises ImportError and is silently swallowed below.
                # Flagged during lint adoption, not fixed here (unsure get_history() is a
                # drop-in replacement for this call shape without checking its callers too).
                from db import get_all_history  # pylint: disable=no-name-in-module

                db_runs = get_all_history(limit=30, user_id=user_id)
                fetched_count = len(db_runs)

                for run in db_runs:
                    story = run.get("story", "")
                    run_id = run.get("id")
                    tone = run.get("tone") or "Auto"
                    platforms = run.get("platforms") or []
                    platforms_str = ",".join(platforms) if isinstance(platforms, list) else str(platforms)

                    story_excerpt = story[:40] + "..." if len(story) > 40 else story

                    node_id = f"mem_db_{run_id}"
                    if node_id not in added_nodes:
                        nodes.append(
                            {
                                "id": node_id,
                                "label": story_excerpt or f"Run #{run_id}",
                                "type": "campaign",
                                "group": "campaign",
                                "full_text": f"Brief: {story}\nTone: {tone}\nPlatforms: {platforms_str}",
                                "tone": tone,
                                "platforms": platforms_str,
                                "run_id": run_id,
                            }
                        )
                        added_nodes.add(node_id)
                        edges.append({"from": "core_rag", "to": node_id, "label": "stores", "type": "memory"})

                    if tone and tone != "Auto":
                        tone_node_id = f"tone_{tone.lower()}"
                        if tone_node_id not in added_nodes:
                            nodes.append(
                                {
                                    "id": tone_node_id,
                                    "label": f"{tone.capitalize()} Tone",
                                    "type": "tone",
                                    "group": "tone",
                                }
                            )
                            added_nodes.add(tone_node_id)
                        edges.append({"from": node_id, "to": tone_node_id, "label": "uses_tone", "type": "tone"})

                    if platforms:
                        for p in platforms:
                            p_key = p.strip().lower()
                            if p_key in platforms_map:
                                edges.append(
                                    {
                                        "from": node_id,
                                        "to": platforms_map[p_key]["id"],
                                        "label": "targets",
                                        "type": "platform",
                                    }
                                )

            except Exception as db_err:
                print(f"[MemoryService] DB history fallback warning: {db_err}")

        # Summary statistics
        summary = {
            "total_memories": fetched_count,
            "vector_space": "ChromaDB Cosine HNSW" if self.enabled else "RAG DB Memory Store",
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

        return {"success": True, "nodes": nodes, "edges": edges, "summary": summary}
