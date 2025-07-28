#!/usr/bin/env python3
import os
import hmac
import hashlib
import requests
import openai
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime
import re

# === LOAD CONFIGURATION FROM ENVIRONMENT VARIABLES ===
# A function to ensure all required environment variables are set on startup.
def load_env_vars():
    required_vars = [
        "GOOGLE_DRIVE_FOLDER_ID", "OPENAI_API_KEY", "ZOOM_SECRET_TOKEN",
        "ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"
    ]
    config = {}
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            raise ValueError(f"❌ Missing required environment variable: {var}")
        config[var] = value
    return config

try:
    config = load_env_vars()
except ValueError as e:
    print(e)
    exit(1) # Exit if configuration is missing

# Set the OpenAI API key
openai.api_key = config["OPENAI_API_KEY"]

# The prompt for analyzing the consultation
CONSULTATION_FRAMEWORK = """
You are a medical consultation analyst. Your task is to create a clean, professional, and easy-to-read consultation report from a transcript. Use Markdown for formatting.

# Consultation Summary Report

---

## Overview
- **Provider:** [Insert or infer]
- **Consult Duration:** {duration}
- **Overall Score:** [Rate 1–10 based on completeness of the 15-point framework]

## Key Takeaways
*Provide a brief, 2-3 sentence summary of the main points of the consultation, including key recommendations and outcomes.*

---

## Detailed Framework Analysis

1.  **Introduction of Provider:**
2.  **Confirmation of Patient by Name and DOB:**
3.  **Confirmation of Patient Location:**
4.  **Confirmation of Current Regimen:**
5.  **Symptoms, Goals for Treatment:**
6.  **Health Updates, Medication Reconciliation, Preventative Screening:**
7.  **Blood Donation Regimen:**
8.  **Lab Review:**
9.  **HRT/Peptide/Other Recommendations:**
10. **Blood Donation Plans:**
11. **Lab Follow-up Plan:**
12. **Refill Needs:**
13. **CC Confirmation:**
14. **Shipping Address Confirmation:**
15. **Review Plan & Patient Q&A:**

---
*For each of the 15 points above, extract the relevant information. If a section is not discussed, write: “❌ Not addressed.”*
"""

app = FastAPI()

def get_google_access_token():
    """Refreshes the Google API access token using the refresh token."""
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": config["GOOGLE_CLIENT_ID"],
        "client_secret": config["GOOGLE_CLIENT_SECRET"],
        "refresh_token": config["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token"
    }
    response = requests.post(token_url, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        print(f"❌ Failed to refresh Google token: {response.text}")
        raise HTTPException(status_code=500, detail="Failed to refresh Google token")

def get_zoom_access_token():
    """Gets a Server-to-Server OAuth token from Zoom."""
    token_url = "https://zoom.us/oauth/token"
    params = {"grant_type": "account_credentials", "account_id": config["ZOOM_ACCOUNT_ID"]}
    auth = (config["ZOOM_CLIENT_ID"], config["ZOOM_CLIENT_SECRET"])
    
    response = requests.post(token_url, auth=auth, params=params)
    
    if response.status_code == 200:
        print("✅ Successfully obtained Zoom access token.")
        return response.json()["access_token"]
    else:
        print(f"❌ Failed to get Zoom access token: {response.status_code} - {response.text}")
        raise HTTPException(status_code=500, detail="Failed to get Zoom access token")

def extract_provider(text):
    """Extracts the provider's name from the summary text for the filename."""
    match = re.search(r"- \*\*Provider:\*\*\s*(.+)", text)
    if match:
        name = match.group(1).strip()
        return re.sub(r"[^\w\-]", "_", name)
    return "Unknown_Provider"

def upload_to_drive(filename, filedata):
    """Uploads the given file data to the specified Google Drive folder."""
    try:
        access_token = get_google_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        metadata = {
            "name": filename,
            "parents": [config["GOOGLE_DRIVE_FOLDER_ID"]]
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
        raise e


@app.post("/webhook")
async def zoom_webhook(request: Request):
    """Main webhook endpoint to receive notifications from Zoom."""
    body = await request.json()
    event = body.get("event")

    if event == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken", "")
        encrypted_token = hmac.new(
            config["ZOOM_SECRET_TOKEN"].encode(),
            plain_token.encode(),
            hashlib.sha256
        ).hexdigest()
        return JSONResponse(content={"plainToken": plain_token, "encryptedToken": encrypted_token})

    if event == "recording.transcript_completed":
        try:
            payload = body.get("payload", {})
            meeting_object = payload.get("object", {})
            
            meeting_type = meeting_object.get("type")
            entity_id = meeting_object.get("uuid")
            duration = meeting_object.get("duration", "Not available")

            if meeting_type in [1, 2, 3, 4, 8]: # It's a meeting (Instant, Scheduled, Recurring, PMI)
                entity_type = "meetings"
            elif meeting_type in [5, 6, 9]: # It's a webinar
                entity_type = "webinars"
            else:
                print(f"ℹ️ Ignoring unknown meeting type: {meeting_type}")
                return JSONResponse(content={"message": "Event for unknown type ignored"}, status_code=200)

            if not entity_id:
                raise ValueError(f"Entity ID (UUID) not found in webhook payload for type {entity_type}")
            
            if entity_id.startswith('/') or '//' in entity_id:
                entity_id = requests.utils.quote(requests.utils.quote(entity_id, safe=''))

            print(f"ℹ️ Processing transcript for {entity_type[:-1]} UUID: {entity_id}")

            zoom_access_token = get_zoom_access_token()
            headers = {"Authorization": f"Bearer {zoom_access_token}"}
            
            recording_details_url = f"https://api.zoom.us/v2/{entity_type}/{entity_id}/recordings"
            
            recording_details_response = requests.get(recording_details_url, headers=headers)
            recording_details_response.raise_for_status()
            recording_details = recording_details_response.json()

            transcript_file = None
            for file in recording_details.get("recording_files", []):
                if file.get("file_type") == "TRANSCRIPT":
                    transcript_file = file
                    break
            
            if not transcript_file or "download_url" not in transcript_file:
                raise ValueError("Transcript file or download URL not found in API response")

            download_url = transcript_file["download_url"]
            print(f"ℹ️ Found authorized download URL via API: {download_url}")

            transcript_response = requests.get(download_url, headers=headers)
            transcript_response.raise_for_status()
            transcript_text = transcript_response.text

            formatted_prompt = CONSULTATION_FRAMEWORK.format(duration=f"{duration} minutes")

            gpt_response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": formatted_prompt},
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
            
            print(f"✅ Successfully processed transcript for meeting {meeting_object.get('topic')}")
            # --- FIX: Stop processing after the first transcript ---
            # This prevents creating duplicate files if Zoom sends multiple transcript files.
            return JSONResponse(content={"status": "processed"}, status_code=200)

        except requests.exceptions.HTTPError as http_err:
            print(f"❌ HTTP error occurred: {http_err}")
            if http_err.response:
                print(f"❌ Zoom API Response Body: {http_err.response.text}")
            raise HTTPException(status_code=500, detail=f"Zoom API Error: {http_err.response.text if http_err.response else 'Unknown'}")
        except Exception as e:
            print(f"❌ An unexpected error occurred during processing: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    print(f"ℹ️ Received and ignored event: {event}")
    return JSONResponse(content={"message": "Event ignored"}, status_code=200)
