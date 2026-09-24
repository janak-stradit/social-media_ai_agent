import uuid
import json
import numpy as np

from config import Config
from db import engine, Session, MemoryEmbedding


def cosine_similarity(v1, v2):
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0
    return dot_product / (norm_v1 * norm_v2)


class MemoryService:
    def __init__(self):
        self.enabled = True
        try:
            from services.embedding_service import EmbeddingService
            self.embedding_service = EmbeddingService()
        except ImportError:
            self.embedding_service = None

    def store_content(self, content_id, text, metadata=None):
        """Store generated content for retrieval"""
        if not self.enabled or not text:
            return
        try:
            if not self.embedding_service:
                print("[MemoryService] No embedding service available.")
                return
            
            embedding_vector = self.embedding_service.get_embedding(text)
            if not embedding_vector:
                return

            with Session(engine) as session:
                emb = MemoryEmbedding(
                    id=str(content_id),
                    content=text,
                    metadata_json=json.dumps(metadata or {}),
                    embedding_array=json.dumps(embedding_vector)
                )
                session.merge(emb)
                session.commit()
        except Exception as e:
            print(f"[MemoryService] Error storing content: {e}")

    def store_campaign_run(self, run_id, story, content, user_id=None, tone=None, platforms=None):
        """Vectorize and store a completed campaign run into memory"""
        if not self.enabled or not story:
            return

        try:
            if not self.embedding_service:
                return

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

            embedding_vector = self.embedding_service.get_embedding(doc_text)
            if not embedding_vector:
                return

            with Session(engine) as session:
                emb = MemoryEmbedding(
                    id=doc_id,
                    content=doc_text,
                    metadata_json=json.dumps(meta),
                    embedding_array=json.dumps(embedding_vector)
                )
                session.merge(emb)
                session.commit()
            print(f"[MemoryService] Successfully indexed campaign run {doc_id} into PostgreSQL memory.")
        except Exception as e:
            print(f"[MemoryService] Failed to index campaign run: {e}")

    def retrieve_context(self, query_text, user_id=None, n_results=3):
        """Retrieve relevant past campaign context for prompt injection (RAG)"""
        if not self.enabled or not query_text or not self.embedding_service:
            return []

        try:
            query_embedding = self.embedding_service.get_embedding(query_text)
            if not query_embedding:
                return []
            query_vector = np.array(query_embedding)

            with Session(engine) as session:
                all_memories = session.query(MemoryEmbedding).all()
                scored_memories = []
                for mem in all_memories:
                    try:
                        meta = json.loads(mem.metadata_json or "{}")
                        if user_id and meta.get("user_id") != str(user_id):
                            continue
                            
                        mem_vec = np.array(json.loads(mem.embedding_array))
                        score = cosine_similarity(query_vector, mem_vec)
                        scored_memories.append((score, mem.content, meta))
                    except Exception:
                        continue
                
                # Sort by highest score first
                scored_memories.sort(key=lambda x: x[0], reverse=True)
                
                retrieved = []
                for score, doc, meta in scored_memories[:n_results]:
                    retrieved.append({"content": doc, "metadata": meta})
                    
                return retrieved
        except Exception as e:
            print(f"[MemoryService] Context retrieval failed: {e}")
            return []

    def get_trending_hashtags(self, category=None):
        """Retrieve trending hashtags from memory or return default niche recommendations"""
        if not self.enabled or not self.embedding_service:
            return ["#VortexSocial", "#ViralMarketing", "#SocialStrategy"]
        try:
            query_embedding = self.embedding_service.get_embedding(category or "trending hashtags")
            if not query_embedding:
                return ["#VortexSocial", "#AIStrategy", "#GrowthMarketing", "#DigitalGrowth"]
            
            query_vector = np.array(query_embedding)
            
            with Session(engine) as session:
                all_memories = session.query(MemoryEmbedding).all()
                scored_hashtags = []
                for mem in all_memories:
                    try:
                        meta = json.loads(mem.metadata_json or "{}")
                        if meta.get("type") != "hashtag":
                            continue
                        mem_vec = np.array(json.loads(mem.embedding_array))
                        score = cosine_similarity(query_vector, mem_vec)
                        scored_hashtags.append((score, mem.content))
                    except Exception:
                        continue
                
                scored_hashtags.sort(key=lambda x: x[0], reverse=True)
                docs = [h[1] for h in scored_hashtags[:2]]
                
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
            with Session(engine) as session:
                count = session.query(MemoryEmbedding).count()
                return {"total_memories": count}
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
            "Instructions: This memory is a MINOR input - weight it at roughly 20% of your analysis, for "
            "tone/style/structural consistency only. The remaining ~80% MUST come from your own fresh, "
            "independent research and reasoning about the actual topic in the current request - do not "
            "let these past examples substitute for that research, and do not lean on them just because "
            "they exist. Do not copy any company name or self-reference from these past examples (e.g. a "
            "past brand-voice/tone label may have been mistakenly used as a company name) - always use "
            "the company name given in the current request's own instructions instead; these examples "
            "are not a source of truth for facts or the company name.\n"
            "--- END MEMORY CONTEXT ---\n"
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
                "info": "Central PostgreSQL Vector Store holding brand embeddings",
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
                with Session(engine) as session:
                    all_memories = session.query(MemoryEmbedding).all()
                    
                    ids = []
                    documents = []
                    metadatas = []
                    
                    for mem in all_memories:
                        meta = json.loads(mem.metadata_json or "{}")
                        if user_id and meta.get("user_id") != str(user_id):
                            continue
                        ids.append(mem.id)
                        documents.append(mem.content)
                        metadatas.append(meta)

                fetched_count = len(ids)

                # Limit to 50 for the graph visualization
                for doc_id, doc_text, meta in list(zip(ids, documents, metadatas))[:50]:
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

        # Summary statistics
        summary = {
            "total_memories": fetched_count,
            "vector_space": "PostgreSQL Numpy Cosine Search",
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

        return {"success": True, "nodes": nodes, "edges": edges, "summary": summary}
