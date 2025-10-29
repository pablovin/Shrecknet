# Library API Documentation

## Overview

The Library API provides endpoints for managing PDF documents within ontologies. It supports automatic metadata extraction from PDFs, cover image generation, and automatic embedding for semantic search.

## Key Features

- **PDF Upload**: Upload PDF files to the library
- **Automatic Metadata Extraction**: Extract title, authors, and description from PDF metadata
- **Automatic Cover Generation**: Generate cover images from the first page of PDFs
- **Automatic Embedding**: Trigger background jobs to embed PDFs for semantic search
- **Bookmark Management**: Create and manage bookmarks within PDF documents

## Endpoints

### Create Library Item

`POST /libraries/{ontology_id}/items`

Creates a new library item with a PDF file.

**Authentication**: Required (Admin or World Builder role)

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology to add the item to

**Form Data**:
- `file` (required, file): PDF file to upload
- `title` (optional, string): Title of the item. If not provided and `auto_extract_metadata=true`, will be extracted from PDF
- `authors` (optional, string): Authors of the item. If not provided and `auto_extract_metadata=true`, will be extracted from PDF
- `description` (optional, string): Description of the item. If not provided and `auto_extract_metadata=true`, will be extracted from PDF subject field
- `cover_url` (optional, string): URL to cover image. If not provided and `auto_extract_metadata=true`, will be generated from first PDF page
- `auto_extract_metadata` (optional, boolean, default: false): If true, extract metadata from PDF when fields are not manually provided
- `auto_embed` (optional, boolean, default: false): If true, automatically trigger embedding job for semantic search

**Response** (201 Created):
```json
{
  "id": 1,
  "ontology_id": 1,
  "title": "World Building Guide",
  "authors": "John Doe, Jane Smith",
  "description": "A comprehensive guide to creating immersive worlds",
  "cover_url": "http://example.com/media/library/1/1/file.png",
  "pdf_url": "http://example.com/media/library/1/1/content.pdf",
  "added_at": "2025-10-29T12:00:00Z",
  "updated_at": "2025-10-29T12:00:00Z",
  "vectorized": false,
  "last_vectorized_at": null
}
```

**Example Usage**:

1. **Manual metadata (traditional approach)**:
```bash
curl -X POST "http://localhost:8000/libraries/1/items" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F "title=My Document" \
  -F "authors=John Doe" \
  -F "description=A great document"
```

2. **Automatic metadata extraction**:
```bash
curl -X POST "http://localhost:8000/libraries/1/items" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F "auto_extract_metadata=true"
```

3. **Automatic extraction with override**:
```bash
curl -X POST "http://localhost:8000/libraries/1/items" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F "title=Custom Title" \
  -F "auto_extract_metadata=true"
```
In this case, the custom title will be used, but authors and description will be extracted from the PDF.

4. **Automatic extraction with embedding**:
```bash
curl -X POST "http://localhost:8000/libraries/1/items" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F "auto_extract_metadata=true" \
  -F "auto_embed=true"
```

### List Library Items

`GET /libraries/{ontology_id}/items`

Lists all library items in an ontology.

**Authentication**: Required

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `skip` (query, integer, default: 0): Number of items to skip
- `limit` (query, integer, default: 50): Maximum number of items to return

**Response** (200 OK):
```json
[
  {
    "id": 1,
    "ontology_id": 1,
    "title": "World Building Guide",
    "authors": "John Doe, Jane Smith",
    "description": "A comprehensive guide",
    "cover_url": "http://example.com/media/library/1/1/file.png",
    "pdf_url": "http://example.com/media/library/1/1/content.pdf",
    "added_at": "2025-10-29T12:00:00Z",
    "updated_at": "2025-10-29T12:00:00Z",
    "vectorized": false,
    "last_vectorized_at": null
  }
]
```

### Get Library Item

`GET /libraries/{ontology_id}/items/{item_id}`

Retrieves a specific library item.

**Authentication**: Required

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Response** (200 OK): Same as Create Library Item response

### Update Library Item

`PUT /libraries/{ontology_id}/items/{item_id}`

Updates a library item's metadata.

**Authentication**: Required (Admin or World Builder role)

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Request Body**:
```json
{
  "title": "Updated Title",
  "authors": "Updated Authors",
  "description": "Updated Description",
  "cover_url": "http://example.com/new-cover.png",
  "vectorized": true,
  "last_vectorized_at": "2025-10-29T12:00:00Z"
}
```

All fields are optional.

**Response** (200 OK): Updated library item

### Replace PDF Content

`POST /libraries/{ontology_id}/items/{item_id}/content`

Replaces the PDF file of an existing library item.

**Authentication**: Required (Admin or World Builder role)

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Form Data**:
- `file` (required, file): New PDF file

**Response** (200 OK): Updated library item

### Delete Library Item

`DELETE /libraries/{ontology_id}/items/{item_id}`

Deletes a library item and its associated PDF file.

**Authentication**: Required (Admin or World Builder role)

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Response** (204 No Content)

### Trigger PDF Embedding

`POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding`

Manually triggers a background job to embed the PDF for semantic search.

**Authentication**: Required (Admin or World Builder role)

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Response** (202 Accepted):
```json
{
  "message": "Embedding job triggered for library item 1",
  "library_item_id": 1,
  "ontology_id": 1,
  "celery_task_id": "abc-123-def-456"
}
```

### Get Embedding Status

`GET /libraries/{ontology_id}/items/{item_id}/embedding-status`

Gets the embedding status for a library item.

**Authentication**: Required

**Parameters**:
- `ontology_id` (path, integer): ID of the ontology
- `item_id` (path, integer): ID of the library item

**Response** (200 OK):
```json
{
  "library_item_id": 1,
  "ontology_id": 1,
  "vectorized": true,
  "last_vectorized_at": "2025-10-29T12:00:00Z",
  "total_chunks": 150,
  "is_embedded": true
}
```

## PDF Metadata Extraction

The system can automatically extract the following metadata from PDFs:

- **Title**: Extracted from the PDF `/Title` metadata field
- **Authors**: Extracted from the PDF `/Author` metadata field
- **Description**: Extracted from the PDF `/Subject` metadata field
- **Cover Image**: Generated from the first page of the PDF, rendered at 150 DPI and saved as PNG

### How It Works

1. When `auto_extract_metadata=true` is set:
   - The PDF is uploaded and saved
   - Metadata is extracted using PyMuPDF (fitz)
   - The first page is rendered as an image and saved using the media service
   - Extracted values are only used if the corresponding field was not manually provided

2. Manual values always take precedence over extracted values

### Cover Image Storage

Cover images are stored in the media directory at:
```
media/library/{ontology_id}/{item_id}/file.png
```

The URL is automatically generated and stored in the `cover_url` field.

## Automatic Embedding

When `auto_embed=true` is set, a background Celery task is triggered to:

1. Extract text from each page of the PDF
2. Generate embeddings using the configured embedding model
3. Store embeddings in Neo4j for semantic search
4. Mark the item as vectorized

This enables semantic search capabilities through the Librarian agent.

## Bookmarks

The Library API also supports bookmarks (see separate bookmark endpoints documentation for details):

- `GET /libraries/items/{item_id}/bookmarks` - List bookmarks
- `POST /libraries/items/{item_id}/bookmarks` - Create bookmark
- `PUT /libraries/bookmarks/{bookmark_id}` - Update bookmark
- `DELETE /libraries/bookmarks/{bookmark_id}` - Delete bookmark

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Only PDF files are supported"
}
```
or
```json
{
  "detail": "Uploaded PDF exceeds size limit"
}
```

### 401 Unauthorized
```json
{
  "detail": "Not authenticated"
}
```

### 403 Forbidden
```json
{
  "detail": "Insufficient permissions"
}
```

### 404 Not Found
```json
{
  "detail": "Library item not found"
}
```

## Integration Examples

### Python Example

```python
import requests

# Upload with automatic metadata extraction
def upload_pdf_auto(token, ontology_id, pdf_path):
    with open(pdf_path, 'rb') as f:
        files = {'file': f}
        data = {
            'auto_extract_metadata': 'true',
            'auto_embed': 'true'
        }
        headers = {'Authorization': f'Bearer {token}'}
        
        response = requests.post(
            f'http://localhost:8000/libraries/{ontology_id}/items',
            files=files,
            data=data,
            headers=headers
        )
        return response.json()

# Upload with manual metadata
def upload_pdf_manual(token, ontology_id, pdf_path, title, authors):
    with open(pdf_path, 'rb') as f:
        files = {'file': f}
        data = {
            'title': title,
            'authors': authors,
            'description': 'Custom description'
        }
        headers = {'Authorization': f'Bearer {token}'}
        
        response = requests.post(
            f'http://localhost:8000/libraries/{ontology_id}/items',
            files=files,
            data=data,
            headers=headers
        )
        return response.json()
```

### JavaScript Example

```javascript
async function uploadPDF(token, ontologyId, file, autoExtract = false) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('auto_extract_metadata', autoExtract);
  
  const response = await fetch(
    `http://localhost:8000/libraries/${ontologyId}/items`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`
      },
      body: formData
    }
  );
  
  return await response.json();
}
```

## Dependencies

The PDF metadata extraction and cover generation features require:

- **PyPDF2**: For PDF validation and test fixtures
- **PyMuPDF** (fitz): For PDF metadata extraction and image rendering
- **Pillow**: For image processing and optimization

PyMuPDF is the primary library used for metadata extraction and cover generation. It provides robust PDF processing capabilities including metadata reading and page rendering.
