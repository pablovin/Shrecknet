# Forum API Endpoints - Quick Reference

This is a quick reference guide for the forum API endpoints with payload/response examples for frontend integration.

## Base URL
All endpoints are prefixed with `/notes/`

---

## Posts (Previously Notes)

### Create Post
```
POST /notes/
```

**Request:**
```json
{
  "title": "Campaign Ideas",
  "content": "<p>Let's brainstorm...</p>",
  "ontology_id": 5,
  "share_user_ids": [2, 3]
}
```

**Response (201):**
```json
{
  "id": 1,
  "title": "Campaign Ideas",
  "content": "<p>Let's brainstorm...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3],
  "created_at": "2025-12-19T12:00:00Z",
  "updated_at": "2025-12-19T12:00:00Z"
}
```

---

### List My Posts
```
GET /notes/
```

**Response (200):**
```json
[
  {
    "id": 1,
    "title": "Campaign Ideas",
    "content": "<p>Let's brainstorm...</p>",
    "ontology_id": 5,
    "owner_id": 1,
    "shared_with": [2, 3],
    "created_at": "2025-12-19T12:00:00Z",
    "updated_at": "2025-12-19T12:00:00Z"
  }
]
```

---

### List Shared Posts
```
GET /notes/shared
```

**Response (200):**
```json
[
  {
    "id": 2,
    "title": "World Building",
    "content": "<p>Ideas for the world...</p>",
    "ontology_id": 5,
    "owner_id": 2,
    "shared_with": [1, 3],
    "created_at": "2025-12-19T11:00:00Z",
    "updated_at": "2025-12-19T11:30:00Z"
  }
]
```

---

### Get Single Post
```
GET /notes/{note_id}
```

**Response (200):**
```json
{
  "id": 1,
  "title": "Campaign Ideas",
  "content": "<p>Let's brainstorm...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3],
  "created_at": "2025-12-19T12:00:00Z",
  "updated_at": "2025-12-19T12:00:00Z"
}
```

**Errors:**
- `403`: Not authorized (post not shared with user)
- `404`: Post not found

---

### Update Post (Owner Only)
```
PUT /notes/{note_id}
```

**Request:**
```json
{
  "title": "Updated Campaign Ideas",
  "content": "<p>Updated content...</p>",
  "share_user_ids": [2, 3, 4]
}
```

**Response (200):**
```json
{
  "id": 1,
  "title": "Updated Campaign Ideas",
  "content": "<p>Updated content...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4],
  "created_at": "2025-12-19T12:00:00Z",
  "updated_at": "2025-12-19T13:00:00Z"
}
```

**Errors:**
- `403`: Not authorized (not owner or admin)
- `404`: Post not found

---

### Delete Post (Owner Only)
```
DELETE /notes/{note_id}
```

**Response:** `204 No Content`

**Errors:**
- `403`: Not authorized (not owner or admin)
- `404`: Post not found

---

### Share Post (Owner Only)
```
POST /notes/{note_id}/share
```

**Request:**
```json
{
  "user_ids": [4, 5]
}
```

**Response (200):**
```json
{
  "id": 1,
  "title": "Campaign Ideas",
  "content": "<p>Let's brainstorm...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4, 5],
  "created_at": "2025-12-19T12:00:00Z",
  "updated_at": "2025-12-19T12:00:00Z"
}
```

**Errors:**
- `403`: Not authorized (not owner)
- `404`: Post not found
- `400`: Invalid user IDs

---

### Unshare Post (Owner Only)
```
DELETE /notes/{note_id}/share
```

**Request:**
```json
{
  "user_ids": [4]
}
```

**Response (200):**
```json
{
  "id": 1,
  "title": "Campaign Ideas",
  "content": "<p>Let's brainstorm...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 5],
  "created_at": "2025-12-19T12:00:00Z",
  "updated_at": "2025-12-19T12:00:00Z"
}
```

**Errors:**
- `403`: Not authorized (not owner)
- `404`: Post not found

---

## Responses (Forum Comments)

### List Responses
```
GET /notes/{note_id}/responses
```

**Response (200):**
```json
[
  {
    "id": 1,
    "note_id": 1,
    "author_id": 2,
    "author": {
      "id": 2,
      "full_name": "Jane Smith",
      "email": "jane@example.com"
    },
    "content": "<p>Great idea! I think...</p>",
    "created_at": "2025-12-19T12:15:00Z",
    "updated_at": "2025-12-19T12:15:00Z"
  },
  {
    "id": 2,
    "note_id": 1,
    "author_id": 3,
    "author": {
      "id": 3,
      "full_name": "Bob Johnson",
      "email": "bob@example.com"
    },
    "content": "<p>I agree with Jane...</p>",
    "created_at": "2025-12-19T12:20:00Z",
    "updated_at": "2025-12-19T12:20:00Z"
  }
]
```

**Errors:**
- `403`: Not authorized (post not shared with user)
- `404`: Post not found

---

### Create Response
```
POST /notes/{note_id}/responses
```

**Request:**
```json
{
  "content": "<p>This is my response...</p>"
}
```

**Response (201):**
```json
{
  "id": 3,
  "note_id": 1,
  "author_id": 1,
  "author": {
    "id": 1,
    "full_name": "John Doe",
    "email": "john@example.com"
  },
  "content": "<p>This is my response...</p>",
  "created_at": "2025-12-19T12:25:00Z",
  "updated_at": "2025-12-19T12:25:00Z"
}
```

**Errors:**
- `403`: Not authorized (post not shared with user)
- `404`: Post not found

---

### Update Response (Author Only)
```
PUT /notes/{note_id}/responses/{response_id}
```

**Request:**
```json
{
  "content": "<p>Updated response content...</p>"
}
```

**Response (200):**
```json
{
  "id": 3,
  "note_id": 1,
  "author_id": 1,
  "author": {
    "id": 1,
    "full_name": "John Doe",
    "email": "john@example.com"
  },
  "content": "<p>Updated response content...</p>",
  "created_at": "2025-12-19T12:25:00Z",
  "updated_at": "2025-12-19T12:35:00Z"
}
```

**Errors:**
- `403`: Not authorized (not response author)
- `404`: Post or response not found
- `400`: Response doesn't belong to this post

---

### Delete Response (Author or Post Owner)
```
DELETE /notes/{note_id}/responses/{response_id}
```

**Response:** `204 No Content`

**Errors:**
- `403`: Not authorized (not author, post owner, or admin)
- `404`: Post or response not found
- `400`: Response doesn't belong to this post

---

## Permission Matrix

| Endpoint | Who Can Access |
|----------|----------------|
| `POST /notes/` | Any authenticated user |
| `GET /notes/` | Owner (shows their posts) |
| `GET /notes/shared` | Any user (shows posts shared with them) |
| `GET /notes/{id}` | Owner or shared users |
| `PUT /notes/{id}` | Owner or admins only |
| `DELETE /notes/{id}` | Owner or admins only |
| `POST /notes/{id}/share` | Owner only |
| `DELETE /notes/{id}/share` | Owner only |
| `GET /notes/{id}/responses` | Anyone with post access |
| `POST /notes/{id}/responses` | Anyone with post access |
| `PUT /notes/{id}/responses/{id}` | Response author only |
| `DELETE /notes/{id}/responses/{id}` | Response author, post owner, or admins |

---

## Frontend Integration Notes

1. **Authentication**: All requests require JWT token in `Authorization: Bearer <token>` header
2. **Rich Text**: The `content` field accepts HTML (sanitize on frontend)
3. **Timestamps**: All timestamps are ISO 8601 format with timezone
4. **IDs**: All IDs are integers
5. **Shared With**: The `shared_with` field is an array of user IDs
6. **Author Info**: Response objects include nested `author` object with user details
7. **Notifications**: Users receive notifications when posts are shared or responses are added

## Key Behavioral Changes from Old System

1. **Shared users CAN'T edit posts** - they can only respond
2. **Only post authors can edit posts** (+ admins)
3. **Only response authors can edit their responses**
4. **Post owners can delete any response** on their posts
5. **All existing notes preserved** - database migration is automatic

## Example Frontend Flow

### Creating a Discussion Post
```javascript
// 1. Create post
const post = await createPost({
  title: "Campaign Discussion",
  content: "<p>Let's plan the next arc...</p>",
  ontology_id: 5,
  share_user_ids: [2, 3, 4]
});

// 2. Users receive notifications
// 3. Shared users can view and respond
const response = await createResponse(post.id, {
  content: "<p>I suggest we...</p>"
});

// 4. List all responses
const responses = await listResponses(post.id);

// 5. Post owner can update main post
const updated = await updatePost(post.id, {
  content: "<p>Updated based on feedback...</p>"
});
```

### Responding to a Shared Post
```javascript
// 1. Get shared posts
const sharedPosts = await getSharedPosts();

// 2. View a post and its responses
const post = await getPost(sharedPosts[0].id);
const responses = await listResponses(post.id);

// 3. Add your response
const myResponse = await createResponse(post.id, {
  content: "<p>My thoughts on this...</p>"
});

// 4. Edit your own response
const updated = await updateResponse(post.id, myResponse.id, {
  content: "<p>Updated my thoughts...</p>"
});
```
