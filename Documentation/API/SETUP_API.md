# Setup API

## Create Default Worlds

Creates the default world ontologies and entities for the setup wizard.

**Endpoint:** `POST /setup/default-worlds`

**Authentication:** Requires `ADMIN` or `WORLD_BUILDER` role

**Request Body:**
```json
{
  "worlds": ["fantasy", "horror", "scifi"]
}
```

**Response (201):**
```json
{
  "created": [
    {
      "ontology_id": 1,
      "name": "fantasy",
      "entities": [
        {
          "id": 10,
          "name": "Adventures",
          "image_url": "/media/entity/10/file.png"
        },
        {
          "id": 11,
          "name": "Stories",
          "image_url": "/media/entity/11/file.png"
        },
        {
          "id": 12,
          "name": "NPCs",
          "image_url": "/media/entity/12/file.png"
        },
        {
          "id": 13,
          "name": "Players",
          "image_url": "/media/entity/13/file.png"
        },
        {
          "id": 14,
          "name": "Places",
          "image_url": "/media/entity/14/file.png"
        }
      ],
      "relationships": [
        {
          "id": 20,
          "name": "has stories",
          "source_entity_id": 10,
          "destiny_entity_id": 11
        },
        {
          "id": 21,
          "name": "has adventures",
          "source_entity_id": 11,
          "destiny_entity_id": 10
        }
      ]
    }
  ],
  "skipped": []
}
```

**Notes:**
- Only `fantasy`, `horror`, and `scifi` are accepted. Any other values are returned in `skipped`.
- If a world already exists (ontology with the same name), it is skipped.
- Images are copied from `default/images/world/` into `media/entity/{entity_id}/file.png`.
