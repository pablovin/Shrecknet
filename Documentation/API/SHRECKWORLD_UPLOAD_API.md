# ShreckWorld source upload and metadata autofill

## Upload contract

`POST /admin/shreckworlds/{shreckworld_id}/library-items` requires a Shrecknet
bearer token with role `admin` or `world_builder`. Send `multipart/form-data`.

| Field | Location | Contract |
| --- | --- | --- |
| `pdf` | File part | Required source file: `.pdf`, `.docx`, `.md`, or `.txt`; the legacy field name applies to all formats. |
| `auto_extract_metadata` | Form field | Boolean, default `false`. Send `true` to autofill missing details and render a PDF cover. |
| `title` | Form field or query parameter | Required unless autofill is enabled. Maximum 255 characters. |
| `authors` | Form field or query parameter | Optional author display string, maximum 512 characters. |
| `description` | Form field or query parameter | Optional description. |
| `authority_tier` | Query parameter | Default `core_rules`; also `house_rules`, `supplement`, `setting`, `errata`, `custom`. |
| `authority_priority` | Query parameter | Integer; default `100`. |

Existing query-parameter uploads remain supported. When both locations supply
an item detail, the form field takes precedence. Blank values are treated as
missing; nonblank user values take precedence over extracted values.

Autofill runs synchronously before the `201` response; no Celery task or embedding
job is started. Its rules are:

- PDF: read `Title`, `Author`, and `Subject`, with XMP `dc:title`, `dc:creator`,
  and `dc:description` as fallbacks. Multiple XMP creators are joined with commas.
- PDF image: render the first page as PNG, at most 1200 pixels on its longest
  side. This is a cover preview, not an image stored in document metadata.
- DOCX: read `dc:title`, `dc:creator`, and `dc:description` from
  `docProps/core.xml`. No DOCX cover rendering is performed.
- Markdown and text: no embedded metadata extraction or cover rendering.
- Missing title: use the original filename stem, replacing underscores with
  spaces, or `Untitled` when the stem is empty.
- Missing author or description: return `null`. No AI inference or external
  lookup is performed. Extracted titles/authors are truncated to column limits.
- Unreadable, encrypted, malformed, or missing metadata and failed cover rendering
  are best effort: upload still succeeds using available values and fallbacks.
  This does not guarantee that later document indexing will succeed.

The response is the existing `ShreckWorldLibraryItemRead`, including saved
`title`, `authors`, `description`, `cover_path`, and a new nullable `cover_url`.
`cover_url` is a relative API URL, for example
`/shreckworlds/{shreckworld_id}/library-items/{item_id}/cover`.
`cover_path` is retained for compatibility; use `cover_url` for display.
The item remains `unindexed` until indexing is explicitly requested.

Errors: `401` for missing/invalid credentials, `403` for insufficient role,
`404` for an unknown world, `409` for a duplicate title within that world,
`413` for an empty file or one exceeding `SHRECKWORLD_MAX_DOCUMENT_BYTES`,
`415` for an unsupported extension, and `422` for invalid parameters or a missing
nonblank title while autofill is disabled. Storage failures return `500` and
roll back the item and its files.

## Frontend example

```javascript
const body = new FormData();
body.append("pdf", selectedFile);
body.append("auto_extract_metadata", String(autofillEnabled));
if (title.trim()) body.append("title", title);
if (authors.trim()) body.append("authors", authors);
if (description.trim()) body.append("description", description);

const response = await fetch(
  `${shreckworldApi}/admin/shreckworlds/${worldId}/library-items`,
  { method: "POST", headers: { Authorization: `Bearer ${token}` }, body }
);
if (!response.ok) throw new Error(await response.text());
const item = await response.json(); // Use the saved details to refresh the form.

if (item.cover_url) {
  const cover = await fetch(`${shreckworldApi}${item.cover_url}`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (cover.ok) {
    const objectUrl = URL.createObjectURL(await cover.blob());
    coverImage.src = objectUrl;
    // Revoke objectUrl when the image is replaced or the component unmounts.
  }
}
```

Let the browser set the multipart content type and boundary. Covers require bearer
authentication, so fetch a blob instead of using the API URL directly in an
unauthenticated image element. Details remain editable through the existing
`PATCH /admin/shreckworlds/{shreckworld_id}/library-items/{item_id}` endpoint.
There is no frontend application tracked in this repository.

## Cover download and operations

`GET /shreckworlds/{shreckworld_id}/library-items/{item_id}/cover` requires any
valid Shrecknet bearer token, matching source-download authorization. It returns
`image/png`, or `404` for a missing cover/item or an item in a different world.
Covers are never mounted as public static media.

Covers are stored as `cover.png` beside the source in the item's world-scoped
media directory. Item/world deletion removes them with the source. SQL and local
media remain canonical; no Librarian storage, graph, or retrieval changes occur.
The service layer owns extraction and autofill rules; the router maps transport
fields and persists the item and files in the existing upload transaction.

Install the updated ShreckWorld dependencies (including `PyMuPDF`) or rebuild
`shreckworld_api` before rollout. Autofill itself needs no database migration or backfill. Deployments also using
structured indexing must follow the [indexing migration](SHRECKWORLD_INDEXING_API.md). Existing items are unchanged; this option
applies to new uploads. Extraction adds parsing/rendering latency to opted-in
uploads. Rollback is deploying the prior API and disabling the frontend option;
existing source/cover files and the pre-existing `cover_path` column remain valid.

See [ShreckWorld architecture](../Architecture/SHRECKWORLD.md) and
[Python upload example](../../python_sdk/docs/core_workflows/shreckworld-upload.md).
