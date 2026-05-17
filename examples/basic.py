"""One-shot ``Client`` examples — connect, do one thing, disconnect.

Run individual examples by editing the ``main()`` at the bottom.
"""

import asyncio

from liquidchat import Client


async def send_one_message(jwt: str) -> None:
    """Open a websocket, send one chat message, close."""
    client = Client(token=jwt)
    await client.send_message("hello, chat!")


async def validate_token(jwt: str) -> None:
    """Two ways to check a JWT against the live server."""
    client = Client(token=jwt)

    # Forgiving variant: bad creds OR server down both return False.
    if await client.validate():
        print("token works")

    # Strict variant: distinguishes credential rejection from network errors.
    try:
        ok = await client.validate_strict()
        print("credentials accepted" if ok else "credentials rejected")
    except OSError as e:
        print(f"server unreachable: {e}")


async def chained_actions(mod_jwt: str, target_uuid: str) -> None:
    """Run multiple actions on a single websocket via ``Client.session()``."""
    async with Client(token=mod_jwt).session() as s:
        await s.send_message("about to clean up...")
        await s.ban_user(target_uuid)
        await asyncio.sleep(1)
        await s.unban_user(target_uuid)
        await s.send_private_message("notch", "you've been warned")


async def main() -> None:
    # JWT = "<your-jwt-here>"
    # await send_one_message(JWT)
    # await validate_token(JWT)
    print("Edit examples/basic.py to pick an example to run.")


if __name__ == "__main__":
    asyncio.run(main())
