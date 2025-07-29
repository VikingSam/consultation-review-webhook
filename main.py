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
import markdown2

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


# === AI PROMPT FOR DATA EXTRACTION ONLY ===
CONSULTATION_FRAMEWORK_PROMPT = """
You are a medical consultation analyst. Your task is to extract specific pieces of information from the provided transcript.

Respond with a JSON object with the following keys: "patient_name", "overall_score", "key_takeaways", "framework_analysis", and "anomalous_content".

- "patient_name": Extract the patient's full name. If not found, use "Unknown Patient".
- "overall_score": A score from 1-10 based on the completeness of the 15-point framework.
- "key_takeaways": A brief, 2-3 sentence summary of the consultation.
- "framework_analysis": An array of 15 strings. Each string corresponds to one point of the framework below. Extract the relevant information for each point. If a point is not discussed, the string should be "Not addressed."
- "anomalous_content": Extract any content that seems unusual, out of place, or unprofessional for a medical consultation. If none, the string should be "None noted."

Framework Points:
1. Introduction of Provider
2. Confirmation of Patient by Name and DOB
3. Confirmation of Patient Location
4. Confirmation of Current Regimen
5. Symptoms, Goals for Treatment
6. Health Updates, Medication Reconciliation, Preventative Screening
7. Blood Donation Regimen
8. Lab Review
9. HRT/Peptide/Other Recommendations
10. Blood Donation Plans
11. Lab Follow-up Plan
12. Refill Needs
13. CC Confirmation
14. Shipping Address Confirmation
15. Review Plan & Patient Q&A
"""

# === TEMPLATE BASED ON YOUR GOOGLE DOC ===
REPORT_TEMPLATE_MD = """
# Consultation Summary Report

## Overview
- **Provider:** {provider_name}
- **Patient Name:** {patient_name}
- **Consult Duration:** {duration}
- **Overall Score:** {overall_score}/10

## Key Takeaways
{key_takeaways}

## Detailed Framework Analysis
1.  **Introduction of Provider:** {framework_1}
2.  **Confirmation of Patient by Name and DOB:** {framework_2}
3.  **Confirmation of Patient Location:** {framework_3}
4.  **Confirmation of Current Regimen:** {framework_4}
5.  **Symptoms, Goals for Treatment:** {framework_5}
6.  **Health Updates, Medication Reconciliation, Preventative Screening:** {framework_6}
7.  **Blood Donation Regimen:** {framework_7}
8.  **Lab Review:** {framework_8}
9.  **HRT/Peptide/Other Recommendations:** {framework_9}
10. **Blood Donation Plans:** {framework_10}
11. **Lab Follow-up Plan:** {framework_11}
12. **Refill Needs:** {framework_12}
13. **CC Confirmation:** {framework_13}
14. **Shipping Address Confirmation:** {framework_14}
15. **Review Plan & Patient Q&A:** {framework_15}
16. **Anomalous Content:** {anomalous_content}
"""

HTML_SHELL = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Consultation Summary</title>
    <style>
        body {{ font-family: sans-serif; line-height: 1.6; color: #333; margin: 20px; }}
        h1, h2 {{ color: #2c3e50; border-bottom: 1px solid #dee2e6; padding-bottom: 8px; }}
        h1 {{ text-align: center; }}
        li {{ margin-bottom: 10px; }}
    </style>
</head>
<body>{content}</body>
</html>
"""

app = FastAPI()

# In-memory set to track currently processing meetings.
PROCESSING_MEETING_IDS = set()

def get_google_access_token():
    token_url = "https://oauth2.googleapis.com/token"
    data = {"client_id": config["GOOGLE_CLIENT_ID"], "client_secret": config["GOOGLE_CLIENT_SECRET"], "refresh_token": config["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"}
    response = requests.post(token_url, data=data)
    response.raise_for_status()
    return response.json()["access_token"]

def get_zoom_access_token():
    token_url = "https://zoom.us/oauth/token"
    params = {"grant_type": "account_credentials", "account_id": config["ZOOM_ACCOUNT_ID"]}
    auth = (config["ZOOM_CLIENT_ID"], config["ZOOM_CLIENT_SECRET"])
    response = requests.post(token_url, auth=auth, params=params)
    response.raise_for_status()
    print("✅ Successfully obtained Zoom access token.")
    return response.json()["access_token"]

def upload_to_drive(filename, filedata, mime_type):
    access_token = get_google_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    metadata = {"name": filename, "parents": [config["GOOGLE_DRIVE_FOLDER_ID"]]}
    files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": (filename, filedata, mime_type)}
    response = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", headers=headers, files=files)
    print("📤 Google Drive upload response:", response.status_code, response.text)
    response.raise_for_status()

def is_already_processed(meeting_uuid):
    try:
        access_token = get_google_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        query = f"name contains '{meeting_uuid}' and '{config['GOOGLE_DRIVE_FOLDER_ID']}' in parents and trashed=false"
        params = {'q': query, 'fields': 'files(id)'}
        response = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params)
        response.raise_for_status()
        if response.json().get("files"):
            print(f"✅ Report for meeting {meeting_uuid} already exists. Skipping.")
            return True
        return False
    except Exception as e:
        print(f"⚠️ Could not check for existing file. Error: {e}")
        return False
        
def format_provider_from_email(email):
    """Cleans an email address into a formatted name."""
    if not email or '@' not in email:
        return "Unknown_Provider"
    name_part = email.split('@')[0]
    # Replace dots with spaces and capitalize
    formatted_name = name_part.repl
