"""Quick diagnostic script to check Telegram connectivity, webhook status, and pending updates."""

import asyncio
import httpx
from job_radar.config import settings

TOKEN = settings.telegram_bot_token


async def check():
    url = f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
    print(f"Checking webhook info for token {TOKEN[:10]}...")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(url)
            print("Webhook Info Status:", r.status_code)
            data = r.json()
            print("Webhook Info Data:", data)

            webhook_url = data.get("result", {}).get("url")
            if webhook_url:
                print(f"⚠️ A webhook is currently registered: {webhook_url}")
                print("Long-polling (run_local_bot.py) CANNOT receive updates while a webhook is registered!")
                print("Deleting webhook so polling can receive updates...")
                del_r = await client.post(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook")
                print("Delete webhook result:", del_r.json())
            else:
                print("No active webhook. Long-polling is clear to receive updates.")

            # Check bot getMe
            me_r = await client.get(f"https://api.telegram.org/bot{TOKEN}/getMe")
            print("Bot getMe:", me_r.json())

            # Check pending updates
            upd_r = await client.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates?limit=5")
            print("Pending updates count:", len(upd_r.json().get("result", [])))
            for u in upd_r.json().get("result", []):
                msg = u.get("message", {})
                print(f"- From: {msg.get('from', {}).get('first_name')} | Text: {msg.get('text')}")

        except Exception as e:
            print("Network / API Error:", e)


if __name__ == "__main__":
    asyncio.run(check())
