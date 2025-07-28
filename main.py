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
    match = re.search(r"Provider:\s*(.+)", text)
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
        # Re-raise the exception to be caught by the main handler
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
            meeting_uuid = meeting_object.get("uuid")
            
            if not meeting_uuid:
                raise ValueError("Meeting UUID not found in webhook payload")

            print(f"ℹ️ Processing transcript for meeting UUID: {meeting_uuid}")

            zoom_access_token = get_zoom_access_token()
            headers = {"Authorization": f"Bearer {zoom_access_token}"}
            
            # --- NEW, MORE ROBUST METHOD ---
            # Use the Zoom API to get recording details, which provides an authorized download URL
            recording_details_url = f"https://api.zoom.us/v2/meetings/{meeting_uuid}/recordings"
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

            # Download the transcript using the same authorized token
            transcript_response = requests.get(download_url, headers=headers)
            transcript_response.raise_for_status()
            transcript_text = transcript_response.text

            # Analyze the transcript with OpenAI
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
            
            # Create filename and upload the summary
            provider = extract_provider(summary)
            timestamp = datetime.now().strftime("%y-%m-%d_%H-%M")
            filename = f"{timestamp} - {provider}_Consultation_Summary.txt"

            upload_to_drive(filename, summary.encode("utf-8"))
            
            print(f"✅ Successfully processed transcript for meeting {meeting_object.get('topic')}")
            return JSONResponse(content={"status": "processed"}, status_code=200)

        except Exception as e:
            print(f"❌ An error occurred during processing: {e}")
            # Use HTTPException to return a proper error response to Zoom
            raise HTTPException(status_code=500, detail=str(e))

    print(f"ℹ️ Received and ignored event: {event}")
    return JSONResponse(content={"message": "Event ignored"}, status_code=200)

