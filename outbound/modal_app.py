"""Modal deployment for Pipecat Twilio outbound voice bot.

Usage:
    modal serve modal_app.py   # dev mode (temporary URL, live reload)
    modal deploy modal_app.py  # production (permanent URL)

After deploying, send a POST request to /dialout to initiate an outbound call:

    curl -X POST https://<workspace>--twilio-outbound-bot-serve.modal.run/dialout \\
      -H "Content-Type: application/json" \\
      -d '{"to_number": "+15551234567", "from_number": "+15559876543"}'
"""

import modal

# ---------------------------------------------------------------------------
# Modal image – mirrors the deps from pyproject.toml
# ---------------------------------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libopenblas-dev")
    .pip_install(
        "pipecat-ai[websocket,groq,silero,deepgram,rnnoise,runner,local-smart-turn-v3]>=0.0.99",
        "pipecatcloud>=0.2.18",
        "twilio",
        "aiofiles",
        "python-dotenv",
        "requests",
    )
    .run_commands("python -c 'from pyrnnoise import RNNoise; RNNoise(sample_rate=48000)'")
    .add_local_file("bot.py", "/root/bot.py")
)

# ---------------------------------------------------------------------------
# Modal resources
# ---------------------------------------------------------------------------

recordings_vol = modal.Volume.from_name("pipecat-recordings", create_if_missing=True)

app = modal.App("twilio-outbound-bot")

# ---------------------------------------------------------------------------
# ASGI entrypoint
# ---------------------------------------------------------------------------


@app.function(
    image=image,
    secrets=[modal.Secret.from_dotenv(__file__)],
    volumes={"/recordings": recordings_vol},
    scaledown_window=300,
    timeout=600,
)
@modal.asgi_app()
def serve():
    import os
    import traceback

    from fastapi import FastAPI, HTTPException, Request, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from loguru import logger
    from twilio.rest import Client as TwilioClient
    from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

    # Eagerly import bot and pipecat modules at container init (not per-request)
    # so the WebSocket handler doesn't pay import cost when Twilio connects.
    from bot import bot
    from pipecat.runner.types import WebSocketRunnerArguments

    web_app = FastAPI()

    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @web_app.get("/")
    async def health():
        return {"status": "ok"}

    @web_app.post("/dialout")
    async def handle_dialout(request: Request):
        """Initiate an outbound call via Twilio."""
        data = await request.json()
        to_number = data.get("to_number")
        from_number = data.get("from_number")

        if not to_number or not from_number:
            raise HTTPException(status_code=400, detail="to_number and from_number are required")

        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        if not account_sid or not auth_token:
            raise HTTPException(status_code=500, detail="Missing Twilio credentials")

        # Build TwiML URL from the incoming request's host
        host = request.headers.get("host", "")
        twiml_url = f"https://{host}/twiml"
        logger.info(f"Initiating call to {to_number} with TwiML URL: {twiml_url}")

        client = TwilioClient(account_sid, auth_token)
        call = client.calls.create(
            to=to_number, from_=from_number, url=twiml_url, method="POST"
        )

        return {"call_sid": call.sid, "status": "call_initiated", "to_number": to_number}

    @web_app.post("/twiml")
    async def get_twiml(request: Request):
        """Return TwiML instructing Twilio to open a WebSocket stream."""
        form_data = await request.form()
        to_number = form_data.get("To", "")
        from_number = form_data.get("From", "")

        host = request.headers.get("host", "")
        ws_url = f"wss://{host}/ws"
        logger.info(f"TwiML: directing Twilio stream to {ws_url}")

        response = VoiceResponse()
        connect = Connect()
        stream = Stream(url=ws_url)
        stream.parameter(name="to_number", value=to_number)
        stream.parameter(name="from_number", value=from_number)
        connect.append(stream)
        response.append(connect)
        response.pause(length=20)

        return HTMLResponse(content=str(response), media_type="application/xml")

    @web_app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """Accept Twilio Media Stream and run the Pipecat bot pipeline."""
        await websocket.accept()
        logger.info("WebSocket connection accepted for outbound call")

        try:
            runner_args = WebSocketRunnerArguments(websocket=websocket)
            await bot(runner_args)
        except Exception as e:
            logger.error(f"Error in bot pipeline: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
        finally:
            recordings_vol.commit()

    return web_app
