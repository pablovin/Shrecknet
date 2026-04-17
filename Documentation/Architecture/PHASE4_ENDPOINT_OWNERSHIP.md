# Phase 4 Endpoint Ownership Matrix

This matrix defines where monolith endpoints live after extraction.

## Shrecknet-owned (core/world/graph/agents)
- `/auth`, `/users`, `/media-admin`, `/config`
- `/ontologies`, `/ontology-instances`
- `/agents`, `/jobs/architect`, `/jobs/elder`, `/jobs/elder/chats`, `/jobs/librarian`, `/jobs/novelist`
- `/graphrag`, `/llm_status`, `/setup`, `/backups`, `/legacy`, `/imports`, `/logs`
- `/libraries` (world knowledge library)

## ShreckRPG-owned (RPG product)
- `/games`
- `/notes` (chronicles)
- `/notifications` (alerts)
- `/page-visits` (favorite pages / visit-derived UX)

## Contracts replacing cross-domain assumptions
- ShreckRPG must not read Shrecknet DB directly.
- ShreckRPG resolves user/world through:
  - `GET /v1/contracts/users/me`
  - `GET /v1/contracts/users/{user_id}`
  - `GET /v1/contracts/worlds/{world_id}`
- Event sync remains via `POST /v1/integrations/events` in ShreckRPG.

## Phase-4 bridge strategy implemented
- Canonical new APIs remain under `/v1/*`.
- Legacy compatibility routes are mounted in ShreckRPG for:
  - `/games/*`
  - `/notes/*`
  - `/notifications/*`
  - `/page-visits/*`

This enables endpoint-by-endpoint client migration without forcing a single hard cutover.
