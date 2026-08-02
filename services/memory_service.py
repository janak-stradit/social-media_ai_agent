import chromadb
from chromadb.config import Settings
from config import Config
import json
import uuid

class MemoryService:
    def __init__(self):
        try:
            self.client = chromadb.PersistentClient(
                path=Config.CHROMA_PERSIST_DIR,
                settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.client.get_or_create_collection(
                name="social_media_memory",
                metadata={"hnsw:space": "cosine"}
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
            self.collection.add(
                ids=[str(content_id)],
                documents=[text],
                metadatas=[metadata or {}]
            )
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
                for p in ['facebook', 'instagram', 'linkedin']:
                    if p in content and 'caption' in content[p]:
                        cap = content[p]['caption'].get('primary_caption')
                        if cap:
                            sample_captions.append(f"[{p.upper()}]: {cap}")
            
            doc_text = f"Brief: {story}\n" + "\n".join(sample_captions)
            doc_id = f"run_{run_id or uuid.uuid4()}"
            
            meta = {
                "type": "campaign_run",
                "run_id": str(run_id or ""),
                "user_id": str(user_id or ""),
                "tone": str(tone or ""),
                "platforms": ",".join(platforms) if isinstance(platforms, list) else str(platforms or "")
            }
            
            self.collection.add(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[meta]
            )
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
            
            kwargs = {
                "query_texts": [query_text],
                "n_results": min(n_results, 5)
            }
            if where_filter:
                kwargs["where"] = where_filter

            results = self.collection.query(**kwargs)
            
            documents = results.get('documents', [[]])[0]
            metadatas = results.get('metadatas', [[]])[0]
            
            retrieved = []
            for doc, meta in zip(documents, metadatas):
                retrieved.append({
                    "content": doc,
                    "metadata": meta
                })
            return retrieved
        except Exception as e:
            print(f"[MemoryService] Context retrieval failed: {e}")
            return []

    def get_trending_hashtags(self, category=None):
        """Retrieve trending hashtags from memory or return default niche recommendations"""
        if not self.enabled:
            return ["#ContentAI", "#ViralMarketing", "#SocialStrategy"]
        try:
            results = self.collection.query(
                query_texts=[category or "trending hashtags"],
                n_results=2,
                where={"type": "hashtag"}
            )
            docs = results.get('documents', [[]])[0]
            if docs:
                return docs
        except Exception as e:
            print(f"[MemoryService] get_trending_hashtags notice: {e}")
        return ["#ContentAI", "#AIStrategy", "#GrowthMarketing", "#DigitalGrowth"]

    def format_memory_prompt(self, retrieved_items):
        """Format retrieved memories into prompt section for agents"""
        if not retrieved_items:
            return ""
        
        lines = ["\n--- RELEVANT BRAND & CAMPAIGN MEMORY (PAST HIGH-PERFORMING CONTEXT) ---"]
        for idx, item in enumerate(retrieved_items, 1):
            lines.append(f"Memory #{idx}: {item['content']}")
        lines.append("Instructions: Use the brand voice, themes, and winning patterns above for consistency.\n--- END MEMORY CONTEXT ---\n")
        return "\n".join(lines)