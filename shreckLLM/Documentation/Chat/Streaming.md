# Incremental chat streaming

`POST /chat/stream` is the internal incremental counterpart to `/chat` and `/chat/jobs`.
Existing completed-string contracts are unchanged. The new request accepts `provider_id`, `model`,
`messages`, model generation options and optional `metadata`. Message content
may be text or OpenAI-style text/image_url parts containing inline image data URLs;
remote image URLs are rejected. Provider adapters translate to OpenAI, Anthropic
and Ollama formats without routing private images through public storage.

The response is SSE with JSON token deltas and terminal metadata. Errors after
headers become generic error frames. Consumers must require a terminal success
frame and reject truncated streams. Provider/global concurrency limits apply for
the whole stream; disconnect/cancellation closes the iterator and releases capacity.
No retry or hedge happens after a token is emitted. Output/schema validation belongs
to the caller. Provider finish reasons and usage are forwarded when available;
unknown image/structured capabilities remain unknown in the model catalog.

This endpoint uses the gateway's existing internal-network trust boundary. Keep it
behind the same service network/proxy controls as `/chat`; it is not a browser API.
The Character Companion uses this path for provisional deltas, then validates and
commits a separate final result in Shrecknet.
