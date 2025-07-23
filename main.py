from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests, openai, os, re, smtplib
from email.mime.text import MIMEText
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
from pathlib import Path

app = FastAPI()

# Config
openai.api_key = os.getenv("OPENAI_API_KEY")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = "Sam@VikingAlternative.com"
EMAIL_FROM = SMTP_USERNAME
DRIVE_FOLDER_ID = "1vgPJImWT07FEQKmsv8AuHZMxM_-70lzD"  # your Transcribed Files folder

class Payload(BaseModel):
    file_url: str
    filename: str

@app.post("/webhook")
async def handle_vtt(payload: Payload, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_vtt, payload)
    return {"status": "✅ VTT file received", "file": payload.filename}

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

def upload_to_drive(file_path, filename):
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/credentials.json", scopes=['https://www.googleapis.com/auth/drive.file']
    )
    service = build("drive", "v3", credentials=creds)
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype="text/plain")
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    print(f"📤 Uploaded to Drive: {file.get('id')}")

def clean_vtt(raw_text):
    lines = raw_text.splitlines()
    result = []
    for line in lines:
        if re.match(r"^\d\d:\d\d:\d\d\.\d\d\d -->", line):
            continue  # remove timestamps
        if line.strip().isdigit():
            continue  # remove cue numbers
        if line.strip() == "":
            continue  # skip blanks
        result.append(line.strip())
    return " ".join(result)

def evaluate_with_gpt(transcript):
    prompt = f"""
You are a medical consultation evaluator. Here's a transcript of a provider-patient session:

Your tasks:
- Evaluate quality on a scale of 1 to 10
- Provide a short summary
- Flag if:
  - Patient goals were not asked
  - Ancillary meds not discussed
  - Address/phone not verified
  - Follow-up or labs not mentioned
  - Provider avoided or missed answering questions
  - Tone was confrontational
Return all results clearly labeled.

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

def process_vtt(payload: Payload):
    try:
        url = payload.file_url
        filename = payload.filename
        local_path = f"/tmp/{filename}"

        # Download the VTT file
        r = requests.get(url)
        if r.status_code != 200:
            raise Exception(f"Failed to download: {r.status_code}")
        with open(local_path, "wb") as f:
            f.write(r.content)

        print(f"⬇️ Downloaded {filename}")

        # Read and clean the transcript
        with open(local_path, "r", encoding="utf-8") as f:
            raw_vtt = f.read()
        clean_text = clean_vtt(raw_vtt)

        # Evaluate with GPT
        result = evaluate_with_gpt(clean_text)

        # Save results to file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        txt_name = f"Review - {Path(filename).stem}_{timestamp}.txt"
        output_path = f"/tmp/{txt_name}"
        with open(output_path, "w") as f:
            f.write(result)

        print("🧠 Evaluation complete")

        # Upload to Google Drive
        upload_to_drive(output_path, txt_name)

        # Send email
        send_email(f"✅ Consultation Review: {filename}", result)

    except Exception as e:
        print(f"❌ Error: {e}")
        send_email("❌ Failed VTT Review", str(e))
