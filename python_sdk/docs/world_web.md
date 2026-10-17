# World Web

Use `await client.ontologies.world_web(ontology_id)` to read compact, authorized world topology. The typed `AtlasWorldWeb` contains nodes (`id`, `kind`, `name`, owning `page_id`), edges (`source`, `target`, `label`), `ontology_id`, and `truncated`. The response omits narratives and private perspectives and is capped at 20,000 nodes / 60,000 edges. Check `truncated` before treating the response as exhaustive. Requires the additive backend `/ontologies/{ontology_id}/world-web` endpoint; existing scene reads remain compatible.
