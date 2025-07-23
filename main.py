import os
import hmac
import hashlib
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

@app.post("/webhook")
async def zoom_webhook(request: Request):
    body = await request.json()
    event_type = body.get("event")

    # Handle Zoom URL validation event
    if event_type == "endpoint.url_validation":
        plain_token = body.get("payload", {}).get("plainToken")

        if not plain_token:
            return JSONResponse(status_code=400, content={"error": "plainToken missing"})

        secret_token = os.environ.get("ZOOM_SECRET_TOKEN", "")
        hash_obj = hmac.new(
            secret_token.encode(),
            msg=plain_token.encode(),
            digestmod=hashlib.sha256
        )
        encrypted_token = hash_obj.hexdigest()

        return JSONResponse(
            status_code=200,
            content={
                "plainToken": plain_token,
                "encryptedToken": encrypted_token
            }
        )

    # Default handler for other Zoom events
    return JSONResponse(status_code=200, content={"message": "OK"})
