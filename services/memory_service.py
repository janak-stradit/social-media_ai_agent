import chromadb
from chromadb.config import Settings
from config import Config

class MemoryService:
    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=Config.CHROMA_PERSIST_DIR,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name="social_media_memory",
            metadata={"hnsw:space": "cosine"}
        )
    
    def store_content(self, content_id, text, metadata=None):
        """Store generated content for retrieval"""
        self.collection.add(
            ids=[content_id],
            documents=[text],
            metadatas=[metadata or {}]
        )
    
    def retrieve_similar(self, query_text, n_results=5):
        """Retrieve similar past content for inspiration"""
        results = self.collection.query(
            query_texts=[query_text],
            n_results=n_results
        )
        return results
    
    def get_trending_hashtags(self, category=None):
        """Retrieve trending hashtags from memory"""
        # In production, this would query a trends API
        # For now, return from stored memory
        if category:
            results = self.collection.query(
                query_texts=[f"trending hashtags {category}"],
                n_results=10,
                where={"type": "hashtag"}
            )
            return results
        return None