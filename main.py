from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os, hmac, hashlib, base64

app = FastAPI()

# Get Zoom secret token from environment
ZOOM_SECRET_TOKEN = os.getenv("ZOOM_SECRET_TOKEN")

@app.post("/webhook")
async def zoom_webhook(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        print(f"❌ Failed to parse JSON from Zoom: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Zoom URL validation challenge
    if "plainToken" in body:
        plain_token = body["plainToken"]
        print(f"📩 Received plainToken: {plain_token}")

        encrypted_token = base64.b64encode(
            hmac.new(
                key=ZOOM_SECRET_TOKEN.encode(),
                msg=plain_token.encode(),
                digestmod=hashlib.sha256
            ).digest()
        ).decode()

        print(f"🔐 Encrypted token: {encrypted_token}")

        return JSONResponse({
            "plainToken": plain_token,
            "encryptedToken": encrypted_token
        })

    print("⚠️ No plainToken received — not a challenge request.")
    return JSONResponse({"status": "Not a validation challenge"})
