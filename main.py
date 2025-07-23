from fastapi import FastAPI, Request
import hmac
import hashlib
import base64
import os
import logging
from fastapi.responses import JSONResponse

app = FastAPI()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zoom_webhook")

@app.post("/webhook")
async def zoom_webhook(request: Request):
    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8")
        json_data = await request.json()

        logger.info("\n=== Incoming Zoom Webhook ===")
        logger.info(f"Method: {request.method}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Body: {body_str}")

        if "plainToken" in json_data:
            plain_token = json_data["plainToken"]
            secret_token = os.getenv("ZOOM_SECRET_TOKEN", "")
            logger.info(f"Received plainToken: {plain_token}")
            logger.info(f"Using ZOOM_SECRET_TOKEN: {secret_token}")

            if not secret_token:
                return JSONResponse(content={"error": "Missing ZOOM_SECRET_TOKEN"}, status_code=500)

            h = hmac.new(secret_token.encode("utf-8"), msg=plain_token.encode("utf-8"), digestmod=hashlib.sha256)
            encrypted_token = base64.b64encode(h.digest()).decode("utf-8")

            logger.info(f"Returning encryptedToken: {encrypted_token}")
            return {"plainToken": plain_token, "encryptedToken": encrypted_token}

        logger.info("No plainToken in request. Returning 200.")
        return {"status": "received"}

    except Exception as e:
        logger.error(f"Exception in /webhook: {str(e)}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/")
def root():
    return {"message": "Zoom webhook endpoint"}
