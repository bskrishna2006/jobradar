import asyncio
import httpx
from job_radar.config import settings

async def check_gemini():
    key = settings.gemini_api_key
    print(f"Key preview: {key[:8]}... (length: {len(key)})")
    
    urls = [
        f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        f"https://generativelanguage.googleapis.com/v1/models?key={key}",
    ]
    async with httpx.AsyncClient(timeout=10.0) as client:
        for u in urls:
            try:
                r = await client.get(u)
                print(f"URL: {u.split('?')[0]} -> Status {r.status_code}")
                if r.status_code == 200:
                    models = [m['name'] for m in r.json().get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
                    print("Available models:", models[:8])
                else:
                    print("Response:", r.text[:300])
            except Exception as e:
                print("Error:", e)

if __name__ == "__main__":
    asyncio.run(check_gemini())
