from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests, openai, os, re, subprocess, smtplib
from pathlib import Path
from datetime import datetime
from email.mime.text import MIMEText

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = FastAPI()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Email + Drive Config
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = "Sam@VikingAlternative.com"
EMAIL_FROM = SMTP_USERNAME
DRIVE_FOLDER_ID = "1vgPJImWT07FEQKmsv8AuHZMxM_-70lzD"

class Payload(BaseModel):
    file_url: str
    filename: str

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

@app.post("/webhook")
async def webhook(payload: Payload, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_audio, payload)
    return {"status": "✅ Received", "file": payload.filename}

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print("📧 Email sent.")
    except Exception as e:
        print(f"❌ Email failed: {e}")

def upload_to_drive(file_path, filename):
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = service_account.Credentials.from_service_account_file(
        '/etc/secrets/credentials.json', scopes=SCOPES
    )
    service = build('drive', 'v3', credentials=creds)
    file_metadata = {
        'name': filename,
        'parents': [DRIVE_FOLDER_ID]
    }
    media = MediaFileUpload(file_path, mimetype='text/plain')
    uploaded = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    print(f"📤 Uploaded to Drive: {uploaded.get('id')}")

def process_audio(payload: Payload):
    try:
        url = payload.file_url
        filename = payload.filename
        local_path = f"/tmp/{filename}"

        r = requests.get(url)
        if r.status_code != 200:
            raise Exception(f"Download failed: {r.status_code}")
        with open(local_path, "wb") as f:
            f.write(r.content)

        base = Path(local_path).stem
        file_size = os.path.getsize(local_path)
        transcript = ""

        if file_size > MAX_FILE_SIZE:
            split_base = f"/tmp/{base}_part_%03d.m4a"
            subprocess.run([
                "ffmpeg", "-i", local_path,
                "-f", "segment", "-segment_time", "300",
                "-c", "copy", split_base
            ], check=True)
            parts = sorted(Path("/tmp").glob(f"{base}_part_*.m4a"))
        else:
            parts = [Path(local_path)]

        for part in parts:
            with open(part, "rb") as f:
                result = openai.Audio.transcribe("whisper-1", f)
            transcript += result["text"] + "\n"

        # Evaluation
        wc = len(transcript.split())
        duration_flag = "⚠️ Under 20 minutes" if wc / 150 < 20 else ""

        issues = []
        if not re.search(r'goal|objective', transcript, re.I): issues.append("Missing probing questions")
        if not re.search(r'ancillary|supplement', transcript, re.I): issues.append("No ancillary meds")
        if not re.search(r'\d+ ?(mg|ml)|daily|weekly', transcript, re.I): issues.append("No dosage/treatment plan")
        if not re.search(r'verify.*(address|phone)', transcript, re.I): issues.append("No address/phone verification")

        questions = re.findall(r'Patient: (.*\?)', transcript, re.I)
        answered = [q for q in questions if re.search(re.escape(q), transcript.split("Provider:")[-1], re.I)]
        unanswered = set(questions) - set(answered)

        behavior = "🚩 Behavior Flag" if re.search(r'(yell|argue|angry|hostile)', transcript, re.I) else ""
        proceed = "✅ Proceed: Yes" if not issues else "❌ Proceed: No"

        summary = f"""
{proceed}
{duration_flag}
{behavior}

📋 Summary:
{transcript[:1500]}...

❓ Unanswered:
{list(unanswered) if unanswered else 'None'}

🔍 Evaluation:
{', '.join(issues) if issues else 'All checks passed.'}
"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = f"/tmp/Approved - {base}_{timestamp}.txt"
        with open(output_file, "w") as f:
            f.write(transcript + "\n\n---\n\n" + summary)

        upload_to_drive(output_file, Path(output_file).name)
        send_email(
            f"✅ Transcript Finished: {filename}",
            f"The transcript was completed and uploaded to Google Drive:\n\nFilename: {Path(output_file).name}\n\nStatus: {proceed}"
        )

    except Exception as e:
        print(f"❌ Error: {e}")
        send_email("❌ Transcript Failed", str(e))
