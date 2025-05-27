from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import httpx
import os
from typing import Dict

app = FastAPI()

# Replace with your actual Discord webhook URL
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/your_webhook_id/your_webhook_token"
)

class Payload(BaseModel):
    title: str = "No title provided"
    description: str = "No description provided"
    url: str = "No URL provided"

class PlaneWebhook(BaseModel):
    event: str = "No event type provided"
    payload: Payload = Field(default_factory=Payload)

@app.post("/plane-webhook", response_model=Dict[str, str])
async def handle_plane_webhook(data: PlaneWebhook) -> Dict[str, str]:
    # Construct the message to send to Discord
    discord_message = {
        "content": f"**{data.event}**\nTitle: {data.payload.title}"
                   f"\nDescription: {data.payload.description}\nURL: {data.payload.url}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(DISCORD_WEBHOOK_URL, json=discord_message)

    if response.status_code != 204:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to send message to Discord. Status code: {response.status_code}"
        )

    return {"status": "Message forwarded to Discord successfully"}

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host=host, port=port)