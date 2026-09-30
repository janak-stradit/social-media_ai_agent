import sys
import os
sys.path.append(os.getcwd())

from services.memory_service import MemoryService
from services.embedding_service import EmbeddingService

print("Testing Embedding Service initialization...")
emb_service = EmbeddingService()
print("Embedding initialized successfully.")

print("Testing Memory Service retrieval...")
mem_service = MemoryService()
results = mem_service.retrieve_context("Facebook campaign for tech startup", n_results=3)

print("Got Results:")
for r in results:
    print(r["content"][:100], "... =>", r["metadata"])
