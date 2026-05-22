# Shrecknet Python SDK

Async SDK for Shrecknet core non-AI workflows.

## Install

```bash
pip install -e python_sdk
```

## 60-second usage

```python
import asyncio
from shrecknet_client import Shrecknet

async def main() -> None:
    async with Shrecknet(base_url="http://localhost:8100") as sdk:
        await sdk.login("keeper", "change-me-strong")
        me = await sdk.me()
        print(me.username, me.role)

asyncio.run(main())
```

## Full Documentation

- Main docs entry: [docs/index.md](./docs/index.md)
- Auth bootstrap flow: [docs/getting_started/auth-and-bootstrap.md](./docs/getting_started/auth-and-bootstrap.md)
- Generated API reference: [docs/reference/index.md](./docs/reference/index.md)
