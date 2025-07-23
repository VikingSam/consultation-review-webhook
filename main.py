from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import requests, openai, os, re, smtplib, hmac, hashlib, base64
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = FastAPI()

# Environment config
openai.api_key = os.getenv("OPENAI_API_KEY")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = "Sam@VikingAlternative.com"
EMAIL_FROM = SMTP_USERNAME
DRIVE_FOLDER_ID = "1vgPJImWT07FEQKmsv8AuHZMxM_-70lzD"
ZOOM_SECRET_TOKEN = os.getenv("ZOOM_SECRET_TOKEN")

# Email sender
def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print("📧 Email sent.")
    except Exception as e:
        print(f"❌ Email failed: {e}")

# Google Drive uploader
def upload_to_drive(file_path, filename):
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/credentials.json", scopes=['https://www.googleapis.com/auth/drive.file']
    )
    service = build("drive", "v3", credentials=creds)
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype="text/plain")
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    print(f"📤 Uploaded to Drive: {uploaded.get('id')}")

# VTT cleaner
def clean_vtt(raw_text):
    lines = raw_text.splitlines()
    result = []
    for line in lines:
        if re.match(r"^\d\d:\d\d:\d\d\.\d\d\d -->", line):
            continue
        if line.strip().isdigit():
            continue
        if line.strip() == "":
            continue
        result.append(line.strip())
    return " ".join(result)

# GPT evaluator
def evaluate_with_gpt(transcript):
    prompt = f"""
You are a medical consultation evaluator. Here's a transcript of a provider-patient session:

Your tasks:
- Rate quality on a scale of 1 to 10
- Provide a short summary
- Flag if:
  - Patient goals weren't asked
  - Ancillary meds not discussed
  - Follow-up/labs not mentioned
  - Address or phone wasn't verified
  - Provider didn’t answer questions
  - Tone was confrontational

Transcript:
\"\"\"
{transcript}
\"\"\"
"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
        )
        return response["choices"][0]["message"]["content"]
    except Exception as e:
        return f"❌ GPT evaluation failed: {str(e)}"

# Webhook endpoint
@app.post("/webhook")
async def handle_zoom_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception as e:
        print(f"❌ Failed to parse JSON from Zoom: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Zoom challenge validation
    if "plainToken" in body:
        plain_token = body["plainToken"]
        print(f"📩 Received plainToken: {plain_token}")
        encrypted_token = base64.b64encode(
            hmac.new(
                ZOOM_SECRET_TOKEN.encode(),
                plain_token.encode(),
                hashlib.sha256
            ).digest()
        ).decode()
        print(f"🔐 Encrypted token: {encrypted_token}")
        return JSONResponse({
            "plainToken": plain_token,
            "encryptedToken": encrypted_token
        })

    # Process Zoom recording.completed event
    if body.get("event") != "recording.completed":
        return JSONResponse({"status": "ignored", "reason": "not a recording.completed event"})

    recording = body["payload"]["object"]
    access_token = body["payload"].get("download_access_token")
    vtt_url = None
    for file in recording["recording_files"]:
        if file["file_type"] == "VTT":
            vtt_url = file["download_url"]
            break

    if not vtt_url or not access_token:
        return JSONResponse({"status": "ignored", "reason": "no VTT or access token"})

    full_url = f"{vtt_url}?access_token={access_token}"
    filename = f"{recording['id']}.vtt"

    background_tasks.add_task(process_vtt_file, full_url, filename)
    return {"status": "✅ VTT handed to background task"}

# Processing logic
def process_vtt_file(file_url, filename):
    try:
        local_path = f"/tmp/{filename}"
        r = requests.get(file_url)
        if r.status_code != 200:
            raise Exception(f"Download failed: {r.status_code}")
        with open(local_path, "wb") as f:
            f.write(r.content)

        with open(local_path, "r", encoding="utf-8") as f:
            raw_vtt = f.read()

        clean_text = clean_vtt(raw_vtt)
        result = evaluate_with_gpt(clean_text)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_name = f"Review - {Path(filename).stem}_{timestamp}.txt"
        report_path = f"/tmp/{report_name}"
        with open(report_path, "w") as f:
            f.write(result)

        upload_to_drive(report_path, report_name)
        send_email(f"✅ Transcript Reviewed: {filename}", result)

    except Exception as e:
        print(f"❌ Webhook processing failed: {e}")
        send_email("❌ Transcript Review Failed", str(e))
