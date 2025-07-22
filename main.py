from fastapi import FastAPI
from pydantic import BaseModel
import requests, openai, os, re

app = FastAPI()
openai.api_key = os.getenv("OPENAI_API_KEY")

class Payload(BaseModel):
    file_url: str
    filename: str

@app.post("/webhook")
async def review_consult(payload: Payload):
    try:
        print(f"👉 Received: {payload.filename}")
        print(f"📥 Downloading: {payload.file_url}")
        
        # Download the audio file
        r = requests.get(payload.file_url)
        if r.status_code != 200:
            return {"error": f"Failed to download file. Status: {r.status_code}"}

        local = f"/tmp/{payload.filename}"
        with open(local, "wb") as f:
            f.write(r.content)

        # Transcribe
        print(f"🧠 Transcribing with Whisper")
        res = openai.Audio.transcribe("whisper-1", open(local, "rb"))
        txt = res["text"]

        # Estimate duration
        wc = len(txt.split())
        duration_flag = "⚠️ Under 20 minutes" if wc/150 < 20 else ""

        # Evaluation
        issues = []
        if not re.search(r'goal|objective', txt, re.I): issues.append("Missing probing questions")
        if not re.search(r'ancillary|supplement', txt, re.I): issues.append("No ancillary meds")
        if not re.search(r'\d+ ?(mg|ml)|daily|weekly', txt, re.I): issues.append("No dosage/treatment plan")
        if not re.search(r'verify.*(address|phone)', txt, re.I): issues.append("No address/phone verification")

        # Patient questions
        questions = re.findall(r'Patient: (.*\?)', txt, re.I)
        answered = [q for q in questions if re.search(re.escape(q), txt.split("Provider:")[-1], re.I)]
        unanswered = set(questions) - set(answered)

        # Heated exchange
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

        print(f"✅ Success. Saved to {out}")
        return {"result": "done", "filename": payload.filename}

    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        return {"error": str(e)}
