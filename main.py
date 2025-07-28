#!/usr/bin/env python3
import os
import hmac
import hashlib
import requests
import openai
import json
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from datetime import datetime
import re
from weasyprint import HTML

# === LOAD CONFIGURATION FROM ENVIRONMENT VARIABLES ===
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
    exit(1)

# Set the OpenAI API key
openai.api_key = config["OPENAI_API_KEY"]

# The prompt for analyzing the consultation
CONSULTATION_FRAMEWORK = """
You are a medical consultation analyst. Your task is to create a clean, professional, and easy-to-read consultation report from a transcript. Use Markdown for formatting.

# Consultation Summary Report

---

## Overview
- **Provider:** [Insert or infer]
- **Patient Name:** [Extract from transcript, return First Last. If not found, write "Unknown Patient"]
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

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Consultation Summary</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; background-color: #f8f9fa; margin: 0; padding: 20px; }}
        .container {{ max-width: 800px; margin: 20px auto; background-color: #ffffff; border: 1px solid #dee2e6; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); padding: 40px; }}
        h1, h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h1 {{ text-align: center; margin-bottom: 30px; }}
        hr {{ border: none; border-top: 1px solid #dee2e6; margin: 30px 0; }}
        strong {{ color: #34495e; }}
        .not-addressed {{ color: #e74c3c; font-style: italic; }}
    </style>
</head>
<body><div class="container">{report_content}</div></body>
</html>
"""

app = FastAPI()

# In-memory set to track currently processing meetings to prevent race conditions.
PROCESSING_MEETING_IDS = set()

def get_google_access_token():
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
    match = re.search(r"- \*\*Provider:\*\*\s*(.+)", text)
    if match:
        name = match.group(1).strip()
        return re.sub(r"[^\w\s-]", "", name).replace(" ", "_")
    return "Unknown_Provider"

# --- NEW FUNCTION to extract patient name ---
def extract_patient_name(text):
    match = re.search(r"- \*\*Patient Name:\*\*\s*(.+)", text)
    if match:
        name = match.group(1).strip()
        return re.sub(r"[^\w\s-]", "", name).replace(" ", "_")
    return "Unknown_Patient"

def upload_to_drive(filename, filedata, mime_type="text/plain"):
    access_token = get_google_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    metadata = {"name": filename, "parents": [config["GOOGLE_DRIVE_FOLDER_ID"]]}
    files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": (filename, filedata, mime_type)}
    response = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", headers=headers, files=files)
    print("📤 Google Drive upload response:", response.status_code, response.text)
    response.raise_for_status()

def is_already_processed(meeting_uuid):
    """Secondary check: Verifies in Google Drive if a report already exists."""
    try:
        access_token = get_google_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        query = f"name contains '{meeting_uuid}' and '{config['GOOGLE_DRIVE_FOLDER_ID']}' in parents and trashed=false"
        params = {'q': query, 'fields': 'files(id)'}
        response = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params)
        response.raise_for_status()
        if response.json().get("files"):
            print(f"✅ Report for meeting {meeting_uuid} already exists in Drive. Skipping.")
            return True
        return False
    except Exception as e:
        print(f"⚠️ Could not check for existing file, proceeding anyway. Error: {e}")
        return False

async def process_transcript_task(body: dict):
    entity_id = None
    try:
        payload = body.get("payload", {})
        meeting_object = payload.get("object", {})
        entity_id = meeting_object.get("uuid")

        if is_already_processed(entity_id):
            return

        meeting_type = meeting_object.get("type")
        duration = meeting_object.get("duration", "Not available")

        if meeting_type in [1, 2, 3, 4, 8]:
            entity_type = "meetings"
        elif meeting_type in [5, 6, 9]:
            entity_type = "webinars"
        else:
            print(f"ℹ️ Ignoring unknown meeting type: {meeting_type}")
            return

        if not entity_id:
            raise ValueError("Entity ID (UUID) not found in webhook payload")
        
        encoded_entity_id = entity_id
        if encoded_entity_id.startswith('/') or '//' in encoded_entity_id:
            encoded_entity_id = requests.utils.quote(requests.utils.quote(encoded_entity_id, safe=''))

        print(f"ℹ️ Processing transcript for {entity_type[:-1]} UUID: {entity_id}")

        zoom_access_token = get_zoom_access_token()
        headers = {"Authorization": f"Bearer {zoom_access_token}"}
        recording_details_url = f"https://api.zoom.us/v2/{entity_type}/{encoded_entity_id}/recordings"
        recording_details_response = requests.get(recording_details_url, headers=headers)
        recording_details_response.raise_for_status()
        recording_details = recording_details_response.json()

        transcript_file = next((f for f in recording_details.get("recording_files", []) if f.get("file_type") == "TRANSCRIPT"), None)
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
            messages=[{"role": "system", "content": formatted_prompt}, {"role": "user", "content": transcript_text}],
            temperature=0.5, max_tokens=1500
        )
        summary_markdown = gpt_response['choices'][0]['message']['content'].strip()
        
        html_content = summary_markdown.replace('\n', '<br>')
        html_content = re.sub(r'# (.*)', r'<h1>\1</h1>', html_content)
        html_content = re.sub(r'## (.*)', r'<h2>\1</h2>', html_content)
        html_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_content)
        html_content = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html_content)
        html_content = re.sub(r'---', r'<hr>', html_content)
        html_content = re.sub(r'❌ Not addressed.', r'<span class="not-addressed">❌ Not addressed.</span>', html_content)
        final_html = HTML_TEMPLATE.format(report_content=html_content)
        pdf_bytes = HTML(string=final_html).write_pdf()
        
        provider = extract_provider(summary_markdown)
        patient = extract_patient_name(summary_markdown) # Get patient name
        timestamp = datetime.now().strftime("%y-%m-%d_%H-%M")
        
        # --- NEW FILENAME FORMAT with patient name ---
        filename = f"{timestamp} - {provider} - {patient}_Summary.pdf"

        upload_to_drive(filename, pdf_bytes, mime_type="application/pdf")
        
        print(f"✅ Successfully processed transcript for meeting {meeting_object.get('topic')}")

    except Exception as e:
        print(f"❌ An error occurred during background processing: {e}")
    finally:
        if entity_id and entity_id in PROCESSING_MEETING_IDS:
            PROCESSING_MEETING_IDS.remove(entity_id)

@app.post("/webhook")
async def zoom_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    event = body.get("event")

    if event == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken", "")
        encrypted_token = hmac.new(config["ZOOM_SECRET_TOKEN"].encode(), plain_token.encode(), hashlib.sh
