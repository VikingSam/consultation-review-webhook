#!/usr/bin/env python3
import os
import hmac
import hashlib
import requests
import openai
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from datetime import datetime
import re

# === CONFIGURATION ===
GOOGLE_DRIVE_FOLDER_ID = "1vgPJImWT07FEQKmsv8AuHZMxM_-70lzD"
CLIENT_ID = "568018325347-876cpju1r1vi74plg2if4cjpndt82cvs.apps.googleusercontent.com"
CLIENT_SECRET = "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"
REFRESH_TOKEN = "1//04-ZUcXO9Fc7_CgYIARAAGAQSNwF-L9IrOhMxOd0VSyqwwKyDeTGKckJN9AVDviPNtsYhIrtIthIEmbR57Jct92wJnK95omD_LvU"
OPENAI_API_KEY = "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"
ZOOM_SECRET_TOKEN = "yWb5GD-lSdOLQFwyXu1lCA"

openai.api_key = OPENAI_API_KEY

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
        raise Exception(f"❌ Failed to refresh token: {response.text}")

def extract_provider(text):
    match = re.search(r"Provider:\s*(.+)", text)
    if match:
        name = match.group(1).strip()
        return re.sub(r"[^\w\-]", "_", name)
    return "Unknown"

def upload_to_drive(filename, filedata):
    access_token = get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    metadata = {
        "name": filename,
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    files = {
        "data": ("metadata", json.dumps(metadata), "application/json"),
        "file": ("application.txt", filedata, "text/plain")
    }
    response = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        headers=headers,
        files=files
    )
    print("📤 Google Drive upload:", response.status_code, response.text)

@app.post("/webhook")
async def zoom_webhook(request: Request):
    body = await request.json()
    event = body.get("event")

    # ✅ Validate webhook handshake
    if event == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken", "")
        encrypted_token = hmac.new(
            ZOOM_SECRET_TOKEN.encode(),
            plain_token.encode(),
            hashlib.sha256
        ).hexdigest()
        return JSONResponse(content={"plainToken": plain_token, "encryptedToken": encrypted_token})

    # ✅ Catch recording.complete event
    if event == "recording.completed":
        recording_files = body.get("payload", {}).get("object", {}).get("recording_files", [])
        for file in recording_files:
            if file.get("file_type") == "TRANSCRIPT" and file.get("status") == "completed":
                download_url = file.get("download_url")
                headers = {
                    "Authorization": f"Bearer {body['download_token']}"
                }
                resp = requests.get(download_url, headers=headers)
                if resp.status_code == 200:
                    text = resp.text
                    try:
                        gpt_response = openai.ChatCompletion.create(
                            model="gpt-4o",
                            messages=[
                                {"role": "system", "content": CONSULTATION_FRAMEWORK},
                                {"role": "user", "content": text}
                            ],
                            temperature=0.5,
                            max_tokens=1500
                        )
                        summary = gpt_response['choices'][0]['message']['content'].strip()
                    except Exception as e:
                        summary = f"❌ GPT Error: {e}"
                    
                    provider = extract_provider(summary)
                    timestamp = datetime.now().strftime("%y-%m-%d_%H-%M")
                    filename = f"{timestamp} - {provider}_Consultation_Summary.txt"

                    upload_to_drive(filename, summary.encode("utf-8"))
                    return JSONResponse(content={"status": "processed"}, status_code=200)

    return JSONResponse(content={"message": "ignored"}, status_code=200)
