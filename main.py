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

class Payload(BaseModel):
    file_url: str
    filename: str

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB
EMAIL_TO = "Sam@VikingAlternative.com"
EMAIL_FROM = "no-reply@yourdomain.com"  # Change if you're using Mailgun or similar
SMTP_SERVER = "smtp.mailgun.org"  # Or your preferred SMTP
SMTP_PORT = 587
SMTP_USERNAME = "postmaster@YOUR_DOMAIN.mailgun.org"  # Replace with real creds
SMTP_PASSWORD = "your_smtp_password"

@app.post("/webhook")
async def webhook(payload: Payload, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_audio, payload)
    return {"status": "✅ Received", "file": payload.filename}

def send_email(to_email, subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [to_email], msg.as_string())
        print(f"📧 Email sent to {to_email}")
    except Exception as e:
        print(f"❌ Email failed: {e}")

def upload_to_drive(local_file_path, filename):
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = service_account.Credentials.from_service_account_file(
        '/etc/secrets/credentials.json', scopes=SCOPES
    )
    service = build('drive', 'v3', credentials=creds)

    file_metadata = {
        'name': filename,
        'mimeType': 'text/plain'
    }
    media = MediaFileUpload(local_file_path, mimetype='text/plain')

    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()

    print(f"📤 Uploaded to Drive (file ID: {uploaded.get('id')})")

def process_audio(payload: Payload):
    try:
        url = payload.file_url
        filename = payload.filename
        local_path = f"/tmp/{filename}"

        print(f"⬇️ Downloading: {url}")
        r = requests.get(url)
        if r.status_code != 200:
            print(f"❌ Download failed: {r.status_code}")
            return
        with open(local_path, "wb") as f:
            f.write(r.content)

        file_size = os.path.getsize(local_path)
        base = Path(local_path).stem
        transcript = ""

        if file_size > MAX_FILE_SIZE:
            print("⚠️ File too large, splitting...")
            split_base = f"/tmp/{base}_part_%03d.m4a"
            subprocess.run([
                "ffmpeg", "-i", local_path,
                "-f", "segment", "-segment_time", "300",
                "-c", "copy", split_base
            ], check=True)
            parts = sorted(Path("/tmp").glob(f"{base}_part_*.m4a"))
        else:
            parts = [Path(local_path)]

        for i, part in enumerate(parts):
            print(f"🔍 Transcribing part {i+1}: {part.name}")
            with open(part, "rb") as f:
                result = openai.Audio.transcribe("whisper-1", f)
            transcript += result["text"] + "\n"

        print("🧠 Transcript done. Evaluating...")

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
        send_email(EMAIL_TO, f"✅ Transcript Finished: {filename}", f"Your transcript has been uploaded to Google Drive.\n\nResult: {proceed}")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        send_email(EMAIL_TO, f"❌ Transcript Error", str(e))
