# Forum API Documentation

This document describes the forum-style API for posts (previously called "notes") and responses.

## Overview

The forum functionality allows users to:
- **Create posts** with rich text content
- **Share posts** with other users (all responses are automatically shared)
- **Respond to posts** - users with access can add responses
- **Edit their own posts/responses** - only authors can edit their content
- **Delete responses** - authors or post owners can delete responses

## Key Changes from Previous Notes System

1. **Notes are now Posts**: The underlying table name remains "notes" for backward compatibility, but they function as forum posts
2. **Restricted Editing**: Only post owners (and admins) can edit the post content - shared users can no longer edit
3. **Responses**: Users with access can respond to posts, creating a forum-like discussion
4. **Author-only Response Editing**: Only the response author can edit their response
5. **Moderation**: Post owners can delete any response on their post

## Authentication

All endpoints require JWT authentication. Include the token in the Authorization header:

```
Authorization: Bearer <your_jwt_token>
```

## Posts Endpoints

### 1. Create a Post

**Endpoint**: `POST /notes/`

**Description**: Create a new post and optionally share it with users.

**Request Body**:
```json
{
  "title": "Campaign Planning Session",
  "content": "<p>Let's discuss our next campaign arc. I'm thinking of introducing a new villain...</p>",
  "ontology_id": 5,
  "share_user_ids": [2, 3, 4]
}
```

**Response**: `201 Created`
```json
{
  "id": 15,
  "title": "Campaign Planning Session",
  "content": "<p>Let's discuss our next campaign arc. I'm thinking of introducing a new villain...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4],
  "created_at": "2025-12-19T12:00:00+00:00",
  "updated_at": "2025-12-19T12:00:00+00:00"
}
```

### 2. List My Posts

**Endpoint**: `GET /notes/`

**Description**: List all posts owned by the current user.

**Response**: `200 OK`
```json
[
  {
    "id": 15,
    "title": "Campaign Planning Session",
    "content": "<p>Let's discuss our next campaign arc...</p>",
    "ontology_id": 5,
    "owner_id": 1,
    "shared_with": [2, 3, 4],
    "created_at": "2025-12-19T12:00:00+00:00",
    "updated_at": "2025-12-19T12:00:00+00:00"
  }
]
```

### 3. List Shared Posts

**Endpoint**: `GET /notes/shared`

**Description**: List all posts shared with the current user.

**Response**: `200 OK`
```json
[
  {
    "id": 16,
    "title": "World Building Ideas",
    "content": "<p>Here are some ideas for our world...</p>",
    "ontology_id": 5,
    "owner_id": 2,
    "shared_with": [1, 3],
    "created_at": "2025-12-19T11:30:00+00:00",
    "updated_at": "2025-12-19T11:45:00+00:00"
  }
]
```

### 4. Get a Post

**Endpoint**: `GET /notes/{note_id}`

**Description**: Get a specific post by ID. User must be the owner or have the post shared with them.

**Response**: `200 OK`
```json
{
  "id": 15,
  "title": "Campaign Planning Session",
  "content": "<p>Let's discuss our next campaign arc. I'm thinking of introducing a new villain...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4],
  "created_at": "2025-12-19T12:00:00+00:00",
  "updated_at": "2025-12-19T12:00:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User doesn't have access to this post
- `404 Not Found`: Post doesn't exist

### 5. Update a Post

**Endpoint**: `PUT /notes/{note_id}`

**Description**: Update a post. Only the owner or admins can update posts.

**Request Body**:
```json
{
  "title": "Updated Campaign Planning Session",
  "content": "<p>Updated content with more details...</p>",
  "share_user_ids": [2, 3, 4, 5]
}
```

**Response**: `200 OK`
```json
{
  "id": 15,
  "title": "Updated Campaign Planning Session",
  "content": "<p>Updated content with more details...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4, 5],
  "created_at": "2025-12-19T12:00:00+00:00",
  "updated_at": "2025-12-19T12:30:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User is not the owner or admin
- `404 Not Found`: Post doesn't exist

### 6. Delete a Post

**Endpoint**: `DELETE /notes/{note_id}`

**Description**: Delete a post. Only the owner or admins can delete posts. Deleting a post also deletes all its responses.

**Response**: `204 No Content`

**Error Responses**:
- `403 Forbidden`: User is not the owner or admin
- `404 Not Found`: Post doesn't exist

### 7. Share a Post with Users

**Endpoint**: `POST /notes/{note_id}/share`

**Description**: Add users to a post's share list. Only the post owner can share.

**Request Body**:
```json
{
  "user_ids": [6, 7]
}
```

**Response**: `200 OK`
```json
{
  "id": 15,
  "title": "Campaign Planning Session",
  "content": "<p>Let's discuss our next campaign arc...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4, 5, 6, 7],
  "created_at": "2025-12-19T12:00:00+00:00",
  "updated_at": "2025-12-19T12:30:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User is not the post owner
- `404 Not Found`: Post doesn't exist
- `400 Bad Request`: One or more user IDs don't exist

### 8. Unshare a Post from Users

**Endpoint**: `DELETE /notes/{note_id}/share`

**Description**: Remove users from a post's share list. Only the post owner can unshare.

**Request Body**:
```json
{
  "user_ids": [6, 7]
}
```

**Response**: `200 OK`
```json
{
  "id": 15,
  "title": "Campaign Planning Session",
  "content": "<p>Let's discuss our next campaign arc...</p>",
  "ontology_id": 5,
  "owner_id": 1,
  "shared_with": [2, 3, 4, 5],
  "created_at": "2025-12-19T12:00:00+00:00",
  "updated_at": "2025-12-19T12:30:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User is not the post owner
- `404 Not Found`: Post doesn't exist

## Response Endpoints

### 9. List Responses to a Post

**Endpoint**: `GET /notes/{note_id}/responses`

**Description**: List all responses to a post. User must have access to the post.

**Response**: `200 OK`
```json
[
  {
    "id": 1,
    "note_id": 15,
    "author_id": 2,
    "author": {
      "id": 2,
      "full_name": "Jane Smith",
      "email": "jane@example.com"
    },
    "content": "<p>Great idea! I think we should also consider...</p>",
    "created_at": "2025-12-19T12:15:00+00:00",
    "updated_at": "2025-12-19T12:15:00+00:00"
  },
  {
    "id": 2,
    "note_id": 15,
    "author_id": 3,
    "author": {
      "id": 3,
      "full_name": "Bob Johnson",
      "email": "bob@example.com"
    },
    "content": "<p>I agree with Jane. We could also add...</p>",
    "created_at": "2025-12-19T12:20:00+00:00",
    "updated_at": "2025-12-19T12:20:00+00:00"
  }
]
```

**Error Responses**:
- `403 Forbidden`: User doesn't have access to this post
- `404 Not Found`: Post doesn't exist

### 10. Create a Response

**Endpoint**: `POST /notes/{note_id}/responses`

**Description**: Create a response to a post. User must have access to the post.

**Request Body**:
```json
{
  "content": "<p>I have another suggestion. What if we...</p>"
}
```

**Response**: `201 Created`
```json
{
  "id": 3,
  "note_id": 15,
  "author_id": 1,
  "author": {
    "id": 1,
    "full_name": "John Doe",
    "email": "john@example.com"
  },
  "content": "<p>I have another suggestion. What if we...</p>",
  "created_at": "2025-12-19T12:25:00+00:00",
  "updated_at": "2025-12-19T12:25:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User doesn't have access to this post
- `404 Not Found`: Post doesn't exist

### 11. Update a Response

**Endpoint**: `PUT /notes/{note_id}/responses/{response_id}`

**Description**: Update a response. Only the response author can update it.

**Request Body**:
```json
{
  "content": "<p>Updated: I have an even better suggestion. What if we...</p>"
}
```

**Response**: `200 OK`
```json
{
  "id": 3,
  "note_id": 15,
  "author_id": 1,
  "author": {
    "id": 1,
    "full_name": "John Doe",
    "email": "john@example.com"
  },
  "content": "<p>Updated: I have an even better suggestion. What if we...</p>",
  "created_at": "2025-12-19T12:25:00+00:00",
  "updated_at": "2025-12-19T12:35:00+00:00"
}
```

**Error Responses**:
- `403 Forbidden`: User is not the response author
- `404 Not Found`: Post or response doesn't exist
- `400 Bad Request`: Response doesn't belong to this post

### 12. Delete a Response

**Endpoint**: `DELETE /notes/{note_id}/responses/{response_id}`

**Description**: Delete a response. The response author, post owner, or admins can delete responses.

**Response**: `204 No Content`

**Error Responses**:
- `403 Forbidden`: User is not the response author, post owner, or admin
- `404 Not Found`: Post or response doesn't exist
- `400 Bad Request`: Response doesn't belong to this post

## Migration from Old Notes

All existing notes have been automatically preserved and converted to the new post system. The database migration:

1. Keeps all existing notes with their content and share relationships
2. Adds a new `responses` table for forum-style discussions
3. Maintains backward compatibility - the table is still called `notes`

### What Changed for Existing Notes:

- **Behavior Change**: Shared users can no longer edit note content directly
- **New Feature**: Shared users can now respond to notes instead
- **Owner Control**: Only note owners (and admins) can edit note content
- **Sharing**: Sharing behavior remains the same - owners control who can access their posts

## Use Cases

### Example 1: Campaign Discussion

1. GM creates a post about the next campaign arc
2. GM shares the post with all players
3. Players respond with their ideas and suggestions
4. GM can edit the original post to refine the plan
5. Players can edit their own responses
6. GM can delete inappropriate responses if needed

### Example 2: World Building Collaboration

1. World builder creates a post about a new region
2. Shares with other world builders
3. Each world builder responds with ideas
4. Original author updates the post with refined concepts
5. Contributors can update their responses as ideas evolve

### Example 3: Session Notes

1. Player creates post-session notes
2. Shares with the group
3. Other players add their perspectives in responses
4. Original note author keeps the main post updated
5. All responses are preserved for future reference

## Permissions Summary

| Action | Who Can Do It |
|--------|---------------|
| Create Post | Any authenticated user |
| View Post | Owner or users the post is shared with |
| Edit Post | Owner or admins only |
| Delete Post | Owner or admins only |
| Share Post | Owner only |
| Unshare Post | Owner only |
| Create Response | Anyone with access to the post |
| View Responses | Anyone with access to the post |
| Edit Response | Response author only |
| Delete Response | Response author, post owner, or admins |

## Notifications

Users receive notifications when:
- A post is shared with them
- Someone responds to their post
- Someone responds to a post they have access to (except if they're the responder)
