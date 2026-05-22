from .client import AsyncShrecknetClient
from .models import *
from .resources import AgentsAPI, ArchitectAPI, ElderAPI, EmbeddingsAPI, JobsAPI, OntologiesAPI, OntologyEmbeddingsAPI, OntologyInstancesAPI, ShreckLLMAPI, WorldsAPI


class Shrecknet:
    """High-level SDK facade exposing domain APIs and shared auth state."""

    def __init__(
        self,
        base_url: str = "http://localhost:8100",
        token: str | None = None,
        timeout: float = 30.0,
        shreckllm_base_url: str = "http://localhost:8111",
    ):
        self.client = AsyncShrecknetClient(base_url=base_url, token=token, timeout=timeout)
        self.worlds = WorldsAPI(self.client)
        self.ontologies = OntologiesAPI(self.client)
        self.ontology_instances = OntologyInstancesAPI(self.client)
        self.agents = AgentsAPI(self.client)
        self.shreckllm = ShreckLLMAPI(self.client, base_url=shreckllm_base_url, timeout=timeout)
        self.jobs = JobsAPI(self.client)
        self.ontology_embeddings = OntologyEmbeddingsAPI(self.client, self.jobs)
        self.embeddings = EmbeddingsAPI(self.client, self.ontology_embeddings)
        self.elder = ElderAPI(self.client, self.shreckllm, self.agents, self.embeddings)
        self.architect = ArchitectAPI(self.client, self.shreckllm, self.agents, self.jobs)

    async def __aenter__(self) -> "Shrecknet":
        await self.client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.shreckllm.aclose()
        await self.client.__aexit__(exc_type, exc, tb)

    async def login(self, username_or_email: str, password: str):
        """Authenticate and store bearer token on the underlying async client."""
        return await self.client.login(username_or_email, password)

    async def me(self):
        """Return current authenticated user profile from `/users/me`."""
        return await self.client.me()

    async def raw_request(self, method: str, path: str, *, params=None, json=None):
        """Raw escape hatch for uncovered endpoints."""
        return await self.client.raw_request(method, path, params=params, json=json)

    def set_token(self, token: str) -> None:
        """Set bearer token manually."""
        self.client.set_token(token)

    def clear_token(self) -> None:
        """Clear bearer token from current client state."""
        self.client.clear_token()
