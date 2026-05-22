# Getting Started

## Install

```bash
pip install -e python_sdk
```

## Environment Variables

- `SHRECKNET_BASE_URL` default `http://localhost:8100`
- `SHRECKNET_USERNAME` default `keeper`
- `SHRECKNET_PASSWORD` default `change-me-strong`
- `SHRECKNET_EMAIL` default `keeper@example.com`
- `SHRECKNET_FULL_NAME` default `World Keeper`
- `SHRECKNET_TIMEZONE` default `UTC`

## First Run

1. Run `python_sdk/examples/01_login_and_user_creation/02_register_and_login.py`.
2. Then run workflow examples in numeric order.

## 60-second snippet

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
