from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests, openai, os, re, subprocess
from pathlib import Path
from datetime import datetime

app = FastAPI()
openai.api_key = os.getenv("OPENAI_API_KEY")

class Payload(BaseModel):
    file_url: str
    filename: str

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

@app.post("/webhook")
async def webhook(payload: Payload, background_tasks: BackgroundTasks):
    print(f"📦 Received file: {payload.filename}")
    background_tasks.add_task(process_audio, payload)
    return {"status": "✅ Received", "file": payload.filename}

def process_audio(payload: Payload):
    try:
        url = payload.file_url
        filename = payload.filename
        local_path = f"/tmp/{filename}"

        print(f"⬇️ Downloading file from {url}")
        r = requests.get(url)
        if r.status_code != 200:
            print(f"❌ Failed to download: {r.status_code}")
            return
        with open(local_path, "wb") as f:
            f.write(r.content)

        file_size = os.path.getsize(local_path)
        print(f"📏 Size: {file_size} bytes")

        base = Path(local_path).stem
        transcript = ""

        # Split if over 25MB
        if file_size > MAX_FILE_SIZE:
            print("⚠️ File too large, splitting...")
            split_base = f"/tmp/{base}_part_%03d.m4a"
            subprocess.run([
                "ffmpeg", "-i", local_path,
                "-f", "segment", "-segment_time", "300",  # 5 min chunks
                "-c", "copy", split_base
            ], check=True)
            parts = sorted(Path("/tmp").glob(f"{base}_part_*.m4a"))
        else:
            parts = [Path(local_path)]

        for i, part in enumerate(parts):
            print(f"🔍 Transcribing part {i+1}/{len(parts)}: {part.name}")
            with open(part, "rb") as f:
                result = openai.Audio.transcribe("whisper-1", f)
            transcript += result["text"] + "\n"

        print("📄 Transcript complete. Evaluating...")

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

        print(f"✅ Done: {output_file}")
    except Exception as e:
        print(f"❌ Exception: {str(e)}")
