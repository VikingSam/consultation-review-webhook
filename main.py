from fastapi import FastAPI
from pydantic import BaseModel
import requests, openai, os, re, subprocess
from pathlib import Path

app = FastAPI()
openai.api_key = os.getenv("OPENAI_API_KEY")

class Payload(BaseModel):
    file_url: str
    filename: str

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

@app.post("/webhook")
async def review_consult(payload: Payload):
    try:
        print(f"👉 Received: {payload.filename}")
        print(f"📥 Downloading: {payload.file_url}")
        
        # Download
        r = requests.get(payload.file_url)
        if r.status_code != 200:
            return {"error": f"Download failed. Status: {r.status_code}"}

        local_path = f"/tmp/{payload.filename}"
        with open(local_path, "wb") as f:
            f.write(r.content)

        total_size = os.path.getsize(local_path)
        print(f"📏 File size: {total_size} bytes")

        # Split if over 25MB
        if total_size > MAX_FILE_SIZE:
            print("⚠️ File too large. Splitting...")
            base = Path(local_path).stem
            split_base = f"/tmp/{base}_chunk_%03d.m4a"
            subprocess.run([
                "ffmpeg", "-i", local_path,
                "-f", "segment", "-segment_time", "300",
                "-c", "copy", split_base
            ], check=True)
            parts = sorted(Path("/tmp").glob(f"{base}_chunk_*.m4a"))
        else:
            parts = [Path(local_path)]

        # Transcribe each part
        full_text = ""
        for i, part in enumerate(parts):
            print(f"🔍 Transcribing chunk {i+1}: {part.name}")
            with open(part, "rb") as f:
                result = openai.Audio.transcribe("whisper-1", f)
            full_text += result["text"] + "\n"

        # Evaluate transcript
        txt = full_text
        wc = len(txt.split())
        duration_flag = "⚠️ Under 20 minutes" if wc/150 < 20 else ""

        issues = []
        if not re.search(r'goal|objective', txt, re.I): issues.append("Missing probing questions")
        if not re.search(r'ancillary|supplement', txt, re.I): issues.append("No ancillary meds")
        if not re.search(r'\d+ ?(mg|ml)|daily|weekly', txt, re.I): issues.append("No dosage/treatment plan")
        if not re.search(r'verify.*(address|phone)', txt, re.I): issues.append("No address/phone verification")

        questions = re.findall(r'Patient: (.*\?)', txt, re.I)
        answered = [q for q in questions if re.search(re.escape(q), txt.split("Provider:")[-1], re.I)]
        unanswered = set(questions) - set(answered)

        behavior = "🚩 Behavior Flag" if re.search(r'(yell|argue|angry|hostile)', txt, re.I) else ""
        proceed = "✅ Proceed: Yes" if not issues else "❌ Proceed: No"

        summary = f"""
{proceed}
{duration_flag}
{behavior}

📋 Summary:
{txt[:1500]}...

❓ Unanswered:
{list(unanswered) if unanswered else 'None'}

🔍 Evaluation:
{', '.join(issues) if issues else 'All checks passed.'}
"""
        out = f"/tmp/Approved - {payload.filename}.txt"
        with open(out, "w") as f:
            f.write(txt + "\n\n---\n\n" + summary)

        print(f"✅ Done: {out}")
        return {"result": proceed, "file": out}

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return {"error": str(e)}
