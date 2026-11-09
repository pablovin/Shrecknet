# Shrecknet Foundry 0.5.3

- Explicitly use light text and caret colors in the Companion composer, with a
  readable muted placeholder, independent of Foundry's application theme.
- Identify the failed method/route in network errors. Chat/message creation errors
  explain the required Idempotency-Key CORS header. Successful Companion actions
  clear an earlier error notice; ambiguous admission retries keep the original key.
- Correct the deployment example and CORS guide to permit Idempotency-Key.
  Normal browsing can succeed while New chat and Send are blocked by this header.

Requires Foundry VTT **14**. Update the module and reload the table.

Stable manifest:

```text
https://raw.githubusercontent.com/pablovin/Shrecknet/main/integrations/foundry/shrecknet-foundry/module.json
```

Pinned manifest:

```text
https://github.com/pablovin/Shrecknet/releases/download/foundry-v0.5.3/module.json
```

Existing servers must include `Idempotency-Key` in `CORS_ALLOW_HEADERS` in their
repository-root `.env`, then restart the APIs. The module cannot change the
server's CORS policy. See the [CORS rollout guide](../Documentation/Deployment/BROWSER_CORS.md).
No API or database migration is needed. Roll back the plugin using the previous
published archive; keep the required CORS header enabled for Companion admission.

Verification: 100 module tests, five Shrecknet CORS tests and ten shared-policy
tests passed. Chromium checks at 420, 680 and 960 pixels confirm light composer
text and usable layout. Local API preflight accepts the admission header after
restarting the APIs. Live signed-in chat creation and in-Foundry visual acceptance
remain unverified.
