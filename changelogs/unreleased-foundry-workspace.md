# Foundry workspace redesign — shipped in 0.4.0

- Keep Atlas, Shreckworlds, Chronicles, and Companion as the four menus; use a
  responsive icon rail, illustrated bookshelves, and quieter conversation controls.
- Show only ShreckWorld libraries linked to the configured Atlas World. Preserve
  account authorization for Chronicles and Companion.
- Add nullable admin `default_world_id`, setup preselection, and explicit GM rebind.
  Startup adds the column to existing databases; old keys and bindings remain valid.
- Guide GM setup through URL/key, World selection, per-service tests, and explicit
  save. Keep controls in a fixed footer with scrollable content, preserve custom
  service URLs during same-server rebind, and require retesting edited settings.
- Display saved Companion source excerpts and respect selection changes from other clients.

Included in [Foundry 0.4.0](foundry-v0.4.0.md). See the
[deployment guide](../Documentation/Deployment/FOUNDRY_MODULE.md) for rollout,
compatibility, and release verification.
