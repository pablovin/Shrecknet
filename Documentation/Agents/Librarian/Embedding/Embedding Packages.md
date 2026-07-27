# Librarian Embedding Packages

The Librarian embedding-package service moves one complete derived book
embedding between Shrecknet installations without moving the source PDF, SQL
library record, cover, bookmarks, or ontology.

An embedding package is a ZIP response with media type
`application/vnd.shrecknet.librarian-embedding+zip`. It contains
`manifest.json` and `graph.json`. The graph contains the active document's
pages, sections, blocks, parent chunks, child chunks, vectors, and provenance.
This structure is necessary for retrieval, evidence reconstruction, and page
citations; numeric vectors alone are not a usable Librarian embedding.

## Placement and preserved data

Import always takes `ontology_id` and `item_id` from the endpoint URL. Values in
the uploaded package cannot select or overwrite the destination placement. A
new ingestion ID and new page, section, block, and chunk IDs are generated to
avoid collisions.

The package preserves the source display text, embedding text, vectors, page
provenance, headings, parser metadata, embedding model/version, source hash,
and original embedded book/RPG-system context. Consequently, the destination
SQL title may differ from the title encoded into the preserved embedding.

Import verifies the archive shape, SHA-256 checksum, node counts, relationships,
finite vector values, and a single compatible embedding model/dimension. The
configured model ID and dimension must match the package. It does not invoke
Docling or regenerate vectors.

The destination writes the graph in bounded Neo4j transactions (500 rows per
batch), using inactive `PdfChunkCandidate` nodes. After staged-vector
validation succeeds, a short activation transaction promotes the candidates to
active `PdfChunk` nodes. This avoids one large transaction holding every book
node, relationship, and vector in memory. If staging or validation fails, the
new inactive ingestion is removed; an existing active embedding is left
available. On success, the destination SQL item is marked `vectorized` and any
replaced ingestion is removed.

Both endpoints require the `admin` or `world_builder` role. The maximum uploaded
package size is 512 MiB.

## Export endpoint

```http
GET /libraries/{ontology_id}/items/{item_id}/embedding/export
Authorization: Bearer <token>
```

Example:

```bash
curl -f \
  -H "Authorization: Bearer $TOKEN" \
  -o book.shrecknet-embedding \
  http://localhost:8100/libraries/12/items/84/embedding/export
```

Success is `200 OK` with an attachment response. `404` means the destination
library item does not exist; `409` means it has no active structured embedding.

Frontend example:

```ts
const response = await fetch(
  `/libraries/${ontologyId}/items/${itemId}/embedding/export`,
  { headers: { Authorization: `Bearer ${token}` } },
);
if (!response.ok) throw new Error((await response.json()).detail);
const blob = await response.blob();
const url = URL.createObjectURL(blob);
const link = document.createElement("a");
link.href = url;
link.download = `${book.title}.shrecknet-embedding`;
link.click();
URL.revokeObjectURL(url);
```

## Import endpoint

```http
POST /libraries/{ontology_id}/items/{item_id}/embedding/import
Authorization: Bearer <token>
Content-Type: multipart/form-data

file=<embedding package>
```

Example:

```bash
curl -f -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@book.shrecknet-embedding" \
  http://localhost:8100/libraries/12/items/91/embedding/import
```

After the file has been fully received and staged, the endpoint returns `202 Accepted`:

```json
{
  "message": "Embedding file received; preparing the import in the background",
  "status": "queued",
  "library_item_id": 91,
  "ontology_id": 12,
  "celery_task_id": "f1d2d2f9-..."
}
```

Validation and graph activation continue as a background job. Its initial visible
status is `File received; preparing import`; completion details contain the imported
ingestion ID, source metadata, embedding metadata, and node counts.

The frontend should treat `413` as an oversized upload and `422` as an invalid,
corrupt, or runtime-incompatible package. A `404` means the URL's destination
book does not exist.

```ts
const body = new FormData();
body.append("file", selectedFile);
const response = await fetch(
  `/libraries/${ontologyId}/items/${itemId}/embedding/import`,
  {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body,
  },
);
const result = await response.json();
if (!response.ok) throw new Error(result.detail);
```
