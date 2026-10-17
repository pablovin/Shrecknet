# Image uploads and processing

## Purpose

Shrecknet stores user-provided and generated images through the shared
`MediaService`. New images are resized before persistence to reduce pixel count
and the amount of image data clients need to download. This includes user and
companion avatars, media-admin images, library cover previews, and default-world
images.

## Processing contract

- Every stored image is limited to a 640 × 480 bounding box and resized
  proportionally, without enlarging smaller images. Landscape images can be up
  to 640 pixels wide; portrait images can be up to 480 pixels tall.
- The original upload is not retained. The processed image takes its place at
  the same storage path and URL contract used by that upload flow.
- The existing output format and paths are preserved: content images and
  generated covers remain PNG; `save_image` uploads keep their selected filename
  extension and corresponding encoding.
- Pillow optimizes the encoded output. Image conversion also normalizes EXIF
  orientation and does not carry uploaded metadata into the stored derivative.
- The configured `max_image_upload_bytes` limit applies to the incoming file
  before processing; its default remains 5 MiB. Unsupported image formats and
  oversized uploads are rejected rather than stored unchanged.
- The 5 MiB `max_image_upload_bytes` limit remains on the incoming file, before
  resizing. Large-dimension images are accepted when their original file is
  within that limit. Unsupported formats and oversized uploads are rejected.

`image_max_width` and `image_max_height` may be configured below 640 × 480.
Those settings and flow-specific resize requests cannot increase the 640 × 480
ceiling, including on installations with older persisted values. The new limit
affects images written after deployment; existing media is not resized or
rewritten automatically.

## Affected flows

| Flow | Endpoint or owner | Storage/output |
| --- | --- | --- |
| User avatar | `POST /users/{user_id}/avatar` | Avatar URL returned in `UserRead` |
| Personal companion avatar | `POST /users/me/companion/avatar` | Avatar URL returned in `PersonalCompanionAgentRead` |
| Ontology/entity images | `POST /media-admin/images` | Content image URL returned as `url` |
| Library cover preview | PDF cover extraction in the library service/task | PNG cover stored with the library media |
| Default-world artwork | Setup service | PNG artwork stored as the ontology/entity main image |

All of these flows now use the same `MediaService` processing path. PDF document
uploads are not images and are not resized by this policy.

## Compatibility and operations

No API response shape, image URL, storage filename, or database schema changes.
The 640 × 480 maximum applies to new uploads and images generated/copied through
the shared service after deployment. Existing full-size files continue to be
served as-is; there is no automatic backfill. Operators may configure smaller
width and height limits, then re-upload images when they need existing media
regenerated.

Related contracts: [ShreckWorld uploads](./SHRECKWORLD_UPLOAD_API.md),
[PersonalCompanion](../Agents/PersonalCompanion/PersonalCompanion.md), and the
[configuration schema](../../docs/config-schema-v2.md).
