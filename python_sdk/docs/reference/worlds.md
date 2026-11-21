# WorldsAPI

Canonical Shreckworld endpoints.

## Methods

### `list(self)`

List worlds visible to current user.

### `get(self, world_id)`

Fetch a world by id.

### `create(self, *, name, description=None, image_url=None, rpg_system=None, rpg_edition=None, rpg_subversion=None)`

Create a Shreckworld and its empty ontology.

### `update(self, world_id, **metadata)`

Update supplied metadata. Pass `None` to clear an optional field.
