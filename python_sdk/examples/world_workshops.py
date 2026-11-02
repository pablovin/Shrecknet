"""Run a built-in workshop with an existing world; no Agent is created.

Use the SDK client configured as in the SDK quickstart, then call these helpers.
"""
from shrecknet_client.models import ArchitectAnalysisRequest, NovelistRunCreate


async def write_chapter(client, ontology_id: int, text: str):
    return await client.novelist.start_world_run(
        ontology_id, NovelistRunCreate(unstructured_text=text, language="en")
    )


async def analyze_page(client, ontology_id: int, page_id: str):
    return await client.architect.analyze_world(
        ontology_id,
        ArchitectAnalysisRequest(ontology_id=ontology_id, ontology_instance_id=page_id),
    )
