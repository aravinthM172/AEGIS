from pathlib import Path

from app.embeddings import EmbeddingService
from app.vector_store import VectorStore


def ingest_knowledge():

    embedding_service = EmbeddingService()
    vector_store = VectorStore()

    knowledge_path = Path(
        "data/knowledge"
    )

    files = list(
        knowledge_path.glob("*.txt")
    )

    if not files:
        print("No knowledge files found.")
        return

    for file in files:

        text = file.read_text(
            encoding="utf-8"
        )

        # Simple chunking
        chunk_size = 800
        overlap = 100

        chunks = []

        start = 0

        while start < len(text):

            end = start + chunk_size

            chunk = text[start:end]

            chunks.append(chunk)

            start += chunk_size - overlap

        for index, chunk in enumerate(chunks):

            embedding = (
                embedding_service.embed(chunk)
            )

            vector_store.add(
                document_id=f"{file.stem}-{index}",
                text=chunk,
                embedding=embedding,
                metadata={
                    "source": file.name,
                    "chunk": index
                }
            )

        print(
            f"Ingested: {file.name} "
            f"({len(chunks)} chunks)"
        )

    print("")
    print("Knowledge ingestion complete.")


if __name__ == "__main__":
    ingest_knowledge()
