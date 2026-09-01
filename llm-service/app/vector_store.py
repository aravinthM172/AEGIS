import chromadb

from app.config import (
    VECTOR_DB_PATH,
    COLLECTION_NAME
)


class VectorStore:

    def __init__(self):

        self.client = chromadb.PersistentClient(
            path=VECTOR_DB_PATH
        )

        self.collection = (
            self.client.get_or_create_collection(
                name=COLLECTION_NAME
            )
        )

    def add(
        self,
        document_id: str,
        text: str,
        embedding: list[float],
        metadata: dict
    ):

        self.collection.upsert(
            ids=[document_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[metadata]
        )

    def search(
        self,
        embedding: list[float],
        top_k: int = 3
    ):

        return self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k
        )
