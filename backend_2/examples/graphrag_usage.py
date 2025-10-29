"""
Example usage of the GraphRAG module.

This script demonstrates how to use the GraphRAG API endpoints
for embedding and semantic retrieval.
"""

import asyncio
import httpx

# Configuration
API_BASE = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "admin_password"  # Change this


async def get_token(username: str, password: str) -> str:
    """Get authentication token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/auth/token",
            data={"username": username, "password": password},
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def ensure_index(token: str) -> None:
    """Ensure Neo4j vector index exists."""
    print("Creating/verifying vector index...")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/graphrag/index/ensure",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = response.json()
        print(f"  Index: {data['index_name']}")
        print(f"  Model: {data['embedding_model']}")
        print(f"  Dimensions: {data['embedding_dim']}")


async def embed_ontology(token: str, ontology_id: int, batch_size: int = 50) -> None:
    """Embed all nodes in an ontology."""
    print(f"\nEmbedding ontology {ontology_id}...")
    async with httpx.AsyncClient(timeout=600.0) as client:
        response = await client.post(
            f"{API_BASE}/graphrag/embed/ontology",
            headers={"Authorization": f"Bearer {token}"},
            json={"ontology_id": ontology_id, "batch_size": batch_size},
        )
        response.raise_for_status()
        data = response.json()
        print(f"  Processed: {data['nodes_processed']}")
        print(f"  Failed: {data['nodes_failed']}")


async def semantic_search(
    query: str, ontology_id: int | None = None, k: int = 5
) -> dict:
    """Perform semantic search."""
    print(f"\nSearching for: '{query}'")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/graphrag/search",
            json={
                "query": query,
                "ontology_id": ontology_id,
                "k": k,
                "score_threshold": 0.5,
                "include_neighbors": True,
            },
        )
        response.raise_for_status()
        return response.json()


async def get_llm_context(query: str, ontology_id: int | None = None) -> str:
    """Get formatted context for LLM."""
    print(f"\nGetting LLM context for: '{query}'")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/graphrag/context",
            json={"query": query, "ontology_id": ontology_id, "k": 5},
        )
        response.raise_for_status()
        return response.json()["context"]


async def main():
    """Main example workflow."""
    print("GraphRAG Example Usage\n" + "=" * 50)

    # Step 1: Get authentication token
    print("\n1. Authenticating...")
    token = await get_token(USERNAME, PASSWORD)
    print("  ✓ Authenticated")

    # Step 2: Ensure vector index exists
    print("\n2. Setting up vector index...")
    await ensure_index(token)
    print("  ✓ Index ready")

    # Step 3: Embed an ontology (change ID as needed)
    ONTOLOGY_ID = 1
    print(f"\n3. Embedding ontology {ONTOLOGY_ID}...")
    await embed_ontology(token, ONTOLOGY_ID)
    print("  ✓ Ontology embedded")

    # Step 4: Perform semantic search
    print("\n4. Testing semantic search...")
    results = await semantic_search(
        "Who are the vampires in Chicago?", ontology_id=ONTOLOGY_ID
    )
    print(f"\n  Found {results['total']} results:")
    for i, result in enumerate(results["results"][:3], 1):
        print(f"\n  {i}. {result['name']} (score: {result['score']:.3f})")
        print(f"     Labels: {', '.join(result['labels'])}")
        if result.get("neighbors"):
            neighbor_names = [n["name"] for n in result["neighbors"][:3]]
            print(f"     Neighbors: {', '.join(neighbor_names)}")

    # Step 5: Get LLM-ready context
    print("\n5. Getting LLM context...")
    context = await get_llm_context(
        "Tell me about the Camarilla", ontology_id=ONTOLOGY_ID
    )
    print(f"\n  Context preview (first 300 chars):")
    print(f"  {context[:300]}...")

    print("\n" + "=" * 50)
    print("Example completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
