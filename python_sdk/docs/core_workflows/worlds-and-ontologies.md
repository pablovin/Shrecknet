# Worlds and Ontologies Workflow

Run:

```bash
python python_sdk/examples/03_world_management/01_worlds_and_ontologies.py
```

Covers:
- authenticated list worlds
- list ontologies
- fetch ontology details

Use `sdk.worlds.create(name="Britain", rpg_system="Pendragon", rpg_edition="6e")`
for a new world. Its string `id` identifies the world. Pass
`active_ontology_id` to existing ontology, graph and source APIs that accept an
integer scope. An unresolved historical world has a null active scope.
