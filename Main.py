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
    # Download
    r = requests.get(payload.file_url)
    local = f"/tmp/{payload.filename}"
    with open(local, "wb") as f: f.write(r.content)

    # Transcribe
    res = openai.Audio.transcribe("whisper-1", open(local, "rb"))
    txt = res["text"]
    wc = len(txt.split()); flag = "⚠️ Duration Flag: Under 20 minutes" if wc/150 < 20 else ""
    issues = []
    if not re.search(r'goal|objective', txt, re.I): issues.append("Missing probing questions")
    if not re.search(r'ancillary|supplement', txt, re.I): issues.append("No ancillary meds")
    if not re.search(r'\d+ ?(mg|ml)|daily|weekly', txt, re.I): issues.append("No dosage/treatment plan")
    if not re.search(r'verify.*(address|phone)', txt, re.I): issues.append("No address/phone verification")
    questions = re.findall(r'Patient: (.*\?)', txt, re.I)
    answered = [q for q in questions if re.search(re.escape(q), txt.split("Provider:")[-1], re.I)]
    unanswered = set(questions) - set(answered)
    behavior = "🚩 Behavior Flag: Tension detected" if re.search(r'(yell|argue|angry|hostile)', txt, re.I) else ""
    proceed = "✅ Proceed: Yes" if not issues else "❌ Proceed: No"

    summary = f"""
{proceed}
{flag}
{behavior}

📋 Summary:
{txt[:1500]}...

❓ Unanswered:
{list(unanswered) if unanswered else 'None'}

🔍 Evaluation:
{', '.join(issues) if issues else 'All checks passed.'}
"""
    out = f"/tmp/Approved - {payload.filename}.txt"
    with open(out, "w") as f: f.write(txt + "\n\n---\n\n" + summary)
    return {"summary": summary, "output_path": out}