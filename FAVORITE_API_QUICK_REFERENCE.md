# Quick Reference: Favorite Ontology Instances API

## Endpoints for Frontend Integration

### 1. Mark as Favorite
```
POST /ontology-instances/{instance_id}/favorite
Authorization: Bearer {token}
Content-Type: application/json

{
  "ontology_id": 1
}

Response 201:
{
  "id": 1,
  "user_id": 5,
  "instance_id": "abc123",
  "ontology_id": 1,
  "created_at": "2026-01-14T16:30:00Z"
}
```

### 2. Remove from Favorites
```
DELETE /ontology-instances/{instance_id}/favorite
Authorization: Bearer {token}

Response: 204 No Content
```

### 3. Check if Favorited
```
GET /ontology-instances/{instance_id}/is-favorite
Authorization: Bearer {token}

Response 200:
{
  "is_favorite": true
}
```

### 4. List All Favorites
```
GET /ontology-instances/favorites?skip=0&limit=50
Authorization: Bearer {token}

Response 200:
[
  {
    "id": 1,
    "user_id": 5,
    "instance_id": "abc123",
    "ontology_id": 1,
    "created_at": "2026-01-14T16:30:00Z"
  }
]
```

## Notifications

Users receive notifications of type `favorite_instance_update` via the existing notifications API when their favorited instances are updated.

```
GET /notifications/me
Authorization: Bearer {token}

Example notification:
{
  "id": 123,
  "notification_type": "favorite_instance_update",
  "title": "Update to favorited item: Character Name",
  "description": "Content Update: Entities updated",
  "read": false,
  "sent_at": "2026-01-14T18:00:00Z"
}
```

## Quick React Example

```jsx
// Toggle favorite button
const toggleFavorite = async (instanceId, ontologyId) => {
  const isFav = await checkFavorite(instanceId);
  
  if (isFav) {
    await fetch(`/ontology-instances/${instanceId}/favorite`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${token}` }
    });
  } else {
    await fetch(`/ontology-instances/${instanceId}/favorite`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ ontology_id: ontologyId })
    });
  }
};

// List favorites for dashboard
const loadFavorites = async () => {
  const response = await fetch('/ontology-instances/favorites', {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
};
```

For complete documentation with more examples, see `FAVORITE_ONTOLOGY_INSTANCES_API.md`.
