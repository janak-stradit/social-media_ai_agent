import sys
import os
import json
import chromadb
from chromadb.config import Settings

sys.path.append(os.getcwd())
import config
from db import init_db, Session, engine, MemoryEmbedding, CompetitorPostEmbedding

print("Initializing PostgreSQL DB schema...")
init_db()

print("Connecting to ChromaDB via Python Client...")
try:
    client = chromadb.PersistentClient(path='./chroma_db', settings=Settings(anonymized_telemetry=False))
except Exception as e:
    print("Could not load Chroma client. Error:", e)
    sys.exit(1)

def migrate_collection(collection_name, model_class):
    try:
        collection = client.get_collection(collection_name)
    except Exception as e:
        print(f"Collection {collection_name} not found. Skipping.")
        return

    results = collection.get(include=['embeddings', 'metadatas', 'documents'])
    ids = results.get('ids', [])
    embeddings = results.get('embeddings', [])
    documents = results.get('documents', [])
    metadatas = results.get('metadatas', [])

    if not ids:
        print(f"No items in {collection_name}.")
        return

    print(f"Found {len(ids)} items in {collection_name}. Migrating to PostgreSQL...")

    with Session(engine) as session:
        session.query(model_class).delete()
        session.commit()
        
        for i in range(len(ids)):
            emb = model_class(
                id=ids[i],
                content=documents[i] if documents and documents[i] is not None else "",
                metadata_json=json.dumps(metadatas[i]) if metadatas and metadatas[i] else "{}",
                embedding_array=json.dumps(list(embeddings[i])) if embeddings and embeddings[i] is not None else "[]"
            )
            session.add(emb)
            
            if (i + 1) % 500 == 0:
                print(f"Migrated {i + 1} items for {collection_name}...")
                session.commit()
                
        session.commit()
        print(f"Migration complete. Inserted {len(ids)} vectors to {model_class.__tablename__}.")

migrate_collection("social_media_memory", MemoryEmbedding)
migrate_collection("competitor_post_embeddings", CompetitorPostEmbedding)
