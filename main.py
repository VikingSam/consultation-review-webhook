from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os, hmac, hashlib, base64, logging

app = FastAPI()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zoom_webhook")

# Zoom secret token from environment
ZOOM_SECRET_TOKEN = os.getenv("ZOOM_SECRET_TOKEN")

@app.post("/webhook")
async def zoom_webhook(request: Request):
    try:
        body = await request.json()
        logger.info("\n=== Incoming Zoom Webhook ===")
        logger.info(f"Method: {request.method}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Body: {body}")
    except Exception as e:
        logger.error(f"❌ Failed to parse JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Handle Zoom challenge response format: { "payload": { "plainToken": "..." } }
    if "payload" in body and "plainToken" in body["payload"]:
        plain_token = body["payload"]["plainToken"]
        logger.info(f"📩 Received plainToken: {plain_token}")
        logger.info(f"🔐 Using ZOOM_SECRET_TOKEN: {ZOOM_SECRET_TOKEN}")

        if not ZOOM_SECRET_TOKEN:
            return JSONResponse(content={"error": "Missing ZOOM_SECRET_TOKEN"}, status_code=500)

        encrypted_token = base64.b64encode(
            hmac.new(ZOOM_SECRET_TOKEN.encode(), plain_token.encode(), hashlib.sha256).digest()
        ).decode()

        logger.info(f"🔐 Encrypted token: {encrypted_token}")

        return JSONResponse({
            "plainToken": plain_token,
            "encryptedToken": encrypted_token
        })

    logger.info("⚠️ Not a validation request or plainToken missing.")
    return JSONResponse({"status": "Not a validation event"})
