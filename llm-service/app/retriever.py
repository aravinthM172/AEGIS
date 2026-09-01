from app.embeddings import EmbeddingService
from app.vector_store import VectorStore


class Retriever:

    def __init__(self):

        self.embedding_service = (
            EmbeddingService()
        )

        self.vector_store = (
            VectorStore()
        )

    def retrieve(
        self,
        query: str,
        top_k: int = 3
    ):

        embedding = (
            self.embedding_service.embed(
                query
            )
        )

        results = (
            self.vector_store.search(
                embedding,
                top_k
            )
        )

        documents = results.get(
            "documents",
            [[]]
        )[0]

        metadatas = results.get(
            "metadatas",
            [[]]
        )[0]

        return [
            {
                "text": document,
                "metadata": metadata
            }
            for document, metadata
            in zip(documents, metadatas)
        ]
