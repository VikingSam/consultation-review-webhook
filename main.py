import os
import hmac
import hashlib
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/22746281/u2vevch/"  # Your Zapier webhook
ZOOM_SECRET_TOKEN = os.environ.get("ZOOM_SECRET_TOKEN", "")
ZOOM_JWT_TOKEN = os.environ.get("ZOOM_JWT_TOKEN", "")  # Optional if you use OAuth headers instead

@app.post("/webhook")
async def zoom_webhook(request: Request):
    body = await request.json()
    event_type = body.get("event")

    # Handle Zoom URL validation
    if event_type == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken")
        if not plain_token:
            return JSONResponse(status_code=400, content={"error": "plainToken missing"})
        hash_obj = hmac.new(ZOOM_SECRET_TOKEN.encode(), msg=plain_token.encode(), digestmod=hashlib.sha256)
        encrypted_token = hash_obj.hexdigest()
        return JSONResponse(content={"plainToken": plain_token, "encryptedToken": encrypted_token})

    # Handle recording completed events
    if event_type == "recording.completed":
        recording_files = body.get("payload", {}).get("object", {}).get("recording_files", [])
        meeting_topic = body.get("payload", {}).get("object", {}).get("topic", "Unknown_Meeting")
        meeting_start = body.get("payload", {}).get("object", {}).get("start_time", "Unknown_Time")

        for file in recording_files:
            if file.get("file_type") == "VTT":
                download_url = file.get("download_url")
                headers = {"Authorization": f"Bearer {ZOOM_JWT_TOKEN}"}
                transcript_response = requests.get(download_url, headers=headers)

                if transcript_response.status_code == 200:
                    transcript_text = transcript_response.text

                    payload = {
                        "filename": f"{meeting_start}_{meeting_topic}.vtt",
                        "file_text": transcript_text
                    }

                    zapier_response = requests.post(ZAPIER_WEBHOOK_URL, json=payload)
                    print(f"Sent to Zapier. Status: {zapier_response.status_code}")
                else:
                    print(f"Failed to fetch transcript. Status: {transcript_response.status_code}")

        return JSONResponse(content={"message": "Recording processed"})

    return JSONResponse(content={"message": "Ignored"})
