"""Modal deployment for Pipecat Twilio voice bot with latency instrumentation.

Usage:
    modal serve modal_app.py   # dev mode (temporary URL, live reload)
    modal deploy modal_app.py  # production (permanent URL)

After deploying, set your Twilio phone number's incoming call webhook
to the Modal URL (POST method). The TwiML response dynamically routes
Twilio's media stream to the WebSocket endpoint on the same host.
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
        "python-dotenv",
        "requests",
    )
    .run_commands("python -c 'from pyrnnoise import RNNoise; RNNoise(sample_rate=48000)'")
    .add_local_file("bot.py", "/root/bot.py")
    .add_local_file("observers.py", "/root/observers.py")
)

# ---------------------------------------------------------------------------
# Modal resources
# ---------------------------------------------------------------------------

app = modal.App("twilio-latency-bot")

# ---------------------------------------------------------------------------
# ASGI entrypoint
# ---------------------------------------------------------------------------


@app.function(
    image=image,
    secrets=[modal.Secret.from_dotenv(__file__)],
    scaledown_window=300,
    timeout=600,
    keep_warm=0,
)
@modal.asgi_app()
def serve():
    import traceback

    from fastapi import FastAPI, Request, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from loguru import logger

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

    @web_app.post("/")
    async def twiml(request: Request):
        """Return TwiML XML instructing Twilio to open a WebSocket stream."""
        host = request.headers.get("host", "")
        ws_url = f"wss://{host}/ws"
        logger.info(f"TwiML: directing Twilio stream to {ws_url}")

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="{ws_url}"></Stream>'
            "</Connect>"
            '<Pause length="40"/>'
            "</Response>"
        )
        return HTMLResponse(content=xml, media_type="application/xml")

    @web_app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """Accept Twilio Media Stream and run the Pipecat bot pipeline."""
        await websocket.accept()
        logger.info("WebSocket connection accepted")

        try:
            runner_args = WebSocketRunnerArguments(websocket=websocket)
            await bot(runner_args)
        except Exception as e:
            logger.error(f"Error in bot pipeline: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())

    return web_app
