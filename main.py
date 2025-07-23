from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os, hmac, hashlib, base64

app = FastAPI()

@app.post("/webhook")
async def validate(request: Request):
    data = await request.json()

    if "payload" in data and "plainToken" in data["payload"]:
        plain_token = data["payload"]["plainToken"]
        secret = os.getenv("ZOOM_SECRET_TOKEN")

        if not secret:
            return JSONResponse({"error": "Missing ZOOM_SECRET_TOKEN"}, status_code=500)

        encrypted = base64.b64encode(
            hmac.new(secret.encode(), plain_token.encode(), hashlib.sha256).digest()
        ).decode()

        return JSONResponse({
            "plainToken": plain_token,
            "encryptedToken": encrypted
        })

    return JSONResponse({"status": "Not a validation event"})
