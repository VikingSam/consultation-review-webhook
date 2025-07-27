#!/usr/bin/env python3
import os
import hmac
import hashlib
import requests
import openai
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from datetime import datetime
import re

# === CONFIGURATION (LOADED FROM ENVIRONMENT VARIABLES) ===
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ZOOM_SECRET_TOKEN = os.getenv("ZOOM_SECRET_TOKEN")

# Ensure the OpenAI API key is set
openai.api_key = OPENAI_API_KEY

# The prompt for analyzing the consultation
CONSULTATION_FRAMEWORK = """
You are a medical consultation analyst. Read the transcript and extract relevant information for each section of the 15-point framework. Respond with:

Provider: [Insert or infer]
Score: [Rate 1–10 based on completeness]
Consult Duration: [Insert duration in minutes if known or estimate based on transcript]

Summary by Section:
1. **Introduction of Provider**
2. **Confirmation of Patient by Name and DOB**
3. **Confirmation of Patient Location**
4. **Confirmation of Current Regimen**
5. **Symptoms, Goals for Treatment**
6. **Health Updates, Medication Reconciliation, Preventative Screening**
7. **Blood Donation Regimen**
8. **Lab Review**
9. **HRT/Peptide/Other Recommendations**
10. **Blood Donation Plans**
11. **Lab Follow-up Plan**
12. **Refill Needs**
13. **CC Confirmation**
14. **Shipping Address Confirmation**
15. **Review Plan & Patient Q&A**

If a section is not discussed, write: “❌ Not addressed.”
Make the output readable like a professional consultation report.
"""

app = FastAPI()

def get_access_token():
    """Refreshes the Google API access token using the refresh token."""
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        print(f"❌ Failed to refresh token: {response.text}")
        raise Exception(f"❌ Failed to refresh token: {response.text}")

def extract_provider(text):
    """Extracts the provider's name from the summary text for the filename."""
    match = re.search(r"Provider:\s*(.+)", text)
    if match:
        name = match.group(1).strip()
        return re.sub(r"[^\w\-]", "_", name)
    return "Unknown_Provider"

def upload_to_drive(filename, filedata):
    """Uploads the given file data to the specified Google Drive folder."""
    try:
        access_token = get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        metadata = {
            "name": filename,
            "parents": [GOOGLE_DRIVE_FOLDER_ID]
        }
        files = {
            "data": ("metadata", json.dumps(metadata), "application/json"),
            "file": (filename, filedata, "text/plain")
        }
        response = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers=headers,
            files=files
        )
        print("📤 Google Drive upload response:", response.status_code, response.text)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ Error uploading to Google Drive: {e}")


@app.post("/webhook")
async def zoom_webhook(request: Request):
    """Main webhook endpoint to receive notifications from Zoom."""
    body = await request.json()
    event = body.get("event")

    if event == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken", "")
        encrypted_token = hmac.new(
            ZOOM_SECRET_TOKEN.encode(),
            plain_token.encode(),
            hashlib.sha256
        ).hexdigest()
        return JSONResponse(content={"plainToken": plain_token, "encryptedToken": encrypted_token})

    if event == "recording.transcript_completed":
        payload = body.get("payload", {})
        recording_files = payload.get("object", {}).get("recording_files", [])
        
        for file in recording_files:
            if file.get("file_type") == "TRANSCRIPT":
                download_url = file.get("download_url")
                # The download_token is provided in the webhook payload for authorization
                download_token = payload.get("download_token")
                
                # --- DEBUGGING LINES START ---
                print("--- STARTING DOWNLOAD PROCESS ---")
                print(f"ℹ️ Attempting to download from: {download_url}")
                print(f"ℹ️ Using download_token: {download_token}") # This will show if the token is missing (None)
                # --- DEBUGGING LINES END ---
                
                headers = {"Authorization": f"Bearer {download_token}"}
                
                try:
                    transcript_response = requests.get(download_url, headers=headers)
                    print(f"ℹ️ Download request sent. Status code: {transcript_response.status_code}") # Log the response status
                    transcript_response.raise_for_status()
                    transcript_text = transcript_response.text

                    gpt_response = openai.ChatCompletion.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": CONSULTATION_FRAMEWORK},
                            {"role": "user", "content": transcript_text}
                        ],
                        temperature=0.5,
                        max_tokens=1500
                    )
                    summary = gpt_response['choices'][0]['message']['content'].strip()
                    
                    provider = extract_provider(summary)
                    timestamp = datetime.now().strftime("%y-%m-%d_%H-%M")
                    filename = f"{timestamp} - {provider}_Consultation_Summary.txt"

                    upload_to_drive(filename, summary.encode("utf-8"))
                    
                    print(f"✅ Successfully processed transcript for meeting {payload.get('object', {}).get('topic')}")
                    return JSONResponse(content={"status": "processed"}, status_code=200)

                except Exception as e:
                    print(f"❌ An error occurred during processing: {e}")
                    return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=500)

    print(f"ℹ️ Received and ignored event: {event}")
    return JSONResponse(content={"message": "Event ignored"}, status_code=200)
