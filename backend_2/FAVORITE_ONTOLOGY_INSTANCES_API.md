# Favorite Ontology Instances API Documentation

This document describes the API endpoints for managing user favorites for ontology instances.

## Overview

Users can mark ontology instances as "favorites" and will receive notifications when those instances are updated. This includes:
- Content updates (name, entity data, properties, relationships)
- Timeline event creation and updates
- Any other significant changes to the instance

## Endpoints

### 1. Mark Instance as Favorite

Mark an ontology instance as favorite for the current user.

**Endpoint:** `POST /ontology-instances/{instance_id}/favorite`

**Authentication:** Required

**Path Parameters:**
- `instance_id` (string): The ID of the ontology instance to favorite

**Request Body:**
```json
{
  "ontology_id": 1
}
```

**Response:** `201 Created`
```json
{
  "id": 1,
  "user_id": 5,
  "instance_id": "abc123",
  "ontology_id": 1,
  "created_at": "2026-01-14T16:30:00Z"
}
```

**Error Responses:**
- `401 Unauthorized`: User not authenticated
- `404 Not Found`: Ontology instance does not exist

**Example (curl):**
```bash
curl -X POST "http://localhost:8000/ontology-instances/abc123/favorite" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ontology_id": 1}'
```

**Example (JavaScript/fetch):**
```javascript
const response = await fetch(`/ontology-instances/${instanceId}/favorite`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ ontology_id: ontologyId }),
});

if (response.ok) {
  const favorite = await response.json();
  console.log('Added to favorites:', favorite);
}
```

---

### 2. Remove Instance from Favorites

Remove an ontology instance from the current user's favorites.

**Endpoint:** `DELETE /ontology-instances/{instance_id}/favorite`

**Authentication:** Required

**Path Parameters:**
- `instance_id` (string): The ID of the ontology instance to unfavorite

**Response:** `204 No Content`

**Error Responses:**
- `401 Unauthorized`: User not authenticated
- `404 Not Found`: Favorite not found (instance was not favorited by this user)

**Example (curl):**
```bash
curl -X DELETE "http://localhost:8000/ontology-instances/abc123/favorite" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Example (JavaScript/fetch):**
```javascript
const response = await fetch(`/ontology-instances/${instanceId}/favorite`, {
  method: 'DELETE',
  headers: {
    'Authorization': `Bearer ${token}`,
  },
});

if (response.ok) {
  console.log('Removed from favorites');
}
```

---

### 3. Check Favorite Status

Check if an ontology instance is favorited by the current user.

**Endpoint:** `GET /ontology-instances/{instance_id}/is-favorite`

**Authentication:** Required

**Path Parameters:**
- `instance_id` (string): The ID of the ontology instance to check

**Response:** `200 OK`
```json
{
  "is_favorite": true
}
```

**Error Responses:**
- `401 Unauthorized`: User not authenticated

**Example (curl):**
```bash
curl "http://localhost:8000/ontology-instances/abc123/is-favorite" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Example (JavaScript/fetch):**
```javascript
const response = await fetch(`/ontology-instances/${instanceId}/is-favorite`, {
  headers: {
    'Authorization': `Bearer ${token}`,
  },
});

const data = await response.json();
console.log('Is favorite:', data.is_favorite);
```

---

### 4. List User's Favorites

Get a list of all ontology instances favorited by the current user.

**Endpoint:** `GET /ontology-instances/favorites`

**Authentication:** Required

**Query Parameters:**
- `skip` (integer, optional): Number of records to skip for pagination (default: 0)
- `limit` (integer, optional): Maximum number of records to return (default: 50, max: 100)

**Response:** `200 OK`
```json
[
  {
    "id": 1,
    "user_id": 5,
    "instance_id": "abc123",
    "ontology_id": 1,
    "created_at": "2026-01-14T16:30:00Z"
  },
  {
    "id": 2,
    "user_id": 5,
    "instance_id": "xyz789",
    "ontology_id": 1,
    "created_at": "2026-01-14T17:45:00Z"
  }
]
```

**Error Responses:**
- `401 Unauthorized`: User not authenticated

**Example (curl):**
```bash
curl "http://localhost:8000/ontology-instances/favorites?skip=0&limit=20" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Example (JavaScript/fetch):**
```javascript
const response = await fetch('/ontology-instances/favorites?skip=0&limit=20', {
  headers: {
    'Authorization': `Bearer ${token}`,
  },
});

const favorites = await response.json();
console.log('User favorites:', favorites);
```

---

## Notifications

When a user favorites an ontology instance, they will automatically receive notifications when:
- The instance is updated (name change, entity updates)
- Properties are modified
- Relationships are added or changed
- Timeline events are created or updated
- Any other significant changes occur

Notifications will be of type `favorite_instance_update` and can be accessed through the existing notifications API:

**Get User Notifications:**
```bash
GET /notifications/me
```

**Example Notification:**
```json
{
  "id": 123,
  "user_id": 5,
  "notification_type": "favorite_instance_update",
  "title": "Update to favorited item: Character Name",
  "description": "Content Update: Entities, properties, or relationships updated",
  "author_type": "user",
  "author_id": "system",
  "read": false,
  "send_email": false,
  "sent_at": "2026-01-14T18:00:00Z"
}
```

---

## Frontend Integration Example

### React Component Example

```jsx
import React, { useState, useEffect } from 'react';

function FavoriteButton({ instanceId, ontologyId, token }) {
  const [isFavorite, setIsFavorite] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Check favorite status on mount
    checkFavoriteStatus();
  }, [instanceId]);

  const checkFavoriteStatus = async () => {
    try {
      const response = await fetch(
        `/ontology-instances/${instanceId}/is-favorite`,
        {
          headers: { 'Authorization': `Bearer ${token}` },
        }
      );
      const data = await response.json();
      setIsFavorite(data.is_favorite);
    } catch (error) {
      console.error('Error checking favorite status:', error);
    }
  };

  const toggleFavorite = async () => {
    setLoading(true);
    try {
      if (isFavorite) {
        // Remove from favorites
        const response = await fetch(
          `/ontology-instances/${instanceId}/favorite`,
          {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` },
          }
        );
        if (response.ok) {
          setIsFavorite(false);
        }
      } else {
        // Add to favorites
        const response = await fetch(
          `/ontology-instances/${instanceId}/favorite`,
          {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ ontology_id: ontologyId }),
          }
        );
        if (response.ok) {
          setIsFavorite(true);
        }
      }
    } catch (error) {
      console.error('Error toggling favorite:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button onClick={toggleFavorite} disabled={loading}>
      {isFavorite ? '⭐ Favorited' : '☆ Add to Favorites'}
    </button>
  );
}

export default FavoriteButton;
```

### User Dashboard - List Favorites

```jsx
import React, { useState, useEffect } from 'react';

function UserFavorites({ token }) {
  const [favorites, setFavorites] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadFavorites();
  }, []);

  const loadFavorites = async () => {
    try {
      const response = await fetch('/ontology-instances/favorites', {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      const data = await response.json();
      setFavorites(data);
    } catch (error) {
      console.error('Error loading favorites:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div>Loading favorites...</div>;

  return (
    <div className="user-favorites">
      <h2>Your Favorite Instances</h2>
      {favorites.length === 0 ? (
        <p>You haven't favorited any instances yet.</p>
      ) : (
        <ul>
          {favorites.map((fav) => (
            <li key={fav.id}>
              <a href={`/ontology-instances/${fav.instance_id}`}>
                Instance: {fav.instance_id}
              </a>
              <small>Added: {new Date(fav.created_at).toLocaleDateString()}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default UserFavorites;
```

---

## Notes

- Favorites are user-specific - each user has their own list of favorites
- Users can favorite any ontology instance they have access to
- When an instance is updated, ALL users who favorited it will receive a notification
- The notification system uses the existing notification infrastructure
- Notifications can be marked as read through the `/notifications/{notification_id}/read` endpoint
- Users will NOT receive email notifications by default for favorite updates (send_email=false)

---

## Database Schema

For reference, the favorites are stored in the `favorite_ontology_instances` table with the following structure:

```sql
CREATE TABLE favorite_ontology_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    instance_id VARCHAR(255) NOT NULL,
    ontology_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX ix_favorite_ontology_instances_user_id 
    ON favorite_ontology_instances(user_id);
CREATE INDEX ix_favorite_ontology_instances_instance_id 
    ON favorite_ontology_instances(instance_id);
```
