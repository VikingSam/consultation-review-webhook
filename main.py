@app.post("/webhook")
async def handle_zoom_webhook(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        print(f"❌ Failed to parse JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Challenge-response for Zoom URL validation
    if "plainToken" in body:
        try:
            plain_token = body["plainToken"]
            encrypted_token = base64.b64encode(
                hmac.new(
                    ZOOM_SECRET_TOKEN.encode(),
                    plain_token.encode(),
                    hashlib.sha256
                ).digest()
            ).decode()

            return JSONResponse({
                "plainToken": plain_token,
                "encryptedToken": encrypted_token
            })
        except Exception as e:
            print(f"❌ Challenge-response failed: {e}")
            raise HTTPException(status_code=500, detail="Challenge failed")

    return JSONResponse({"status": "✅ Webhook received but not a validation event"})
