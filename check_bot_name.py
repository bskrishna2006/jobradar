import asyncio
import httpx
from job_radar.config import settings

async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe")
        print("BOT_INFO:", r.json())
        upd = await c.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates?limit=10")
        print("UPDATES:", upd.json())

if __name__ == "__main__":
    asyncio.run(main())
