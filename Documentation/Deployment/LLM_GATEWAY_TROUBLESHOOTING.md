# LLM gateway configuration troubleshooting

## Gateway configuration authentication failures

Gateway configuration and provider-validation endpoints delegate the user's bearer
token to `{shrecknet_api_base_url}/users/me`. The gateway uses that direct service
connection with a ten-second timeout, disables inherited environment proxies, and
does not follow redirects with the token. A timeout, connection/protocol failure,
non-authentication upstream error or malformed identity response returns a safe
HTTP 503. It does not become an unhandled 500 or claim that valid credentials are
invalid. Shrecknet 401/403 responses retain the gateway's 401 `Invalid token`
contract; role checks continue to require `admin` or `world_builder`.
Provider validation never runs when identity validation fails. Retry a 503 once
connectivity is restored; it does not require replacing the user's credentials.

In the repository Compose deployment, `configs/shreckllm.initial.json` sets
`shrecknet_api_base_url` to `http://shrecknet_api:8100`. Check that the gateway
container can reach Shrecknet over the Docker service network. An external/public
URL, unreachable service or container network problem can still produce a 503;
the error handling does not restore an unavailable dependency. Rebuild/restart
`shreckllm` to deploy authentication changes. No database migration or credential
rotation is required for this fix.

A provider's own 401 (for example Anthropic `/v1/models`) is independent of this
gateway identity check. Correct or remove that provider's credential in the
provider configuration; OpenRouter catalog success does not validate an Anthropic
key or demonstrate that the Shrecknet identity service is reachable.
