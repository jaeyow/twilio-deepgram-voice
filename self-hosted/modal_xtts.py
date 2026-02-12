"""Modal deployment for XTTS v2 TTS server.

Runs tts_server.py (FastAPI) which implements the XTTS streaming API
compatible with Pipecat's XTTSService.

Usage:
    modal deploy modal_xtts.py   # permanent URL
    modal serve modal_xtts.py    # dev mode (temporary URL)

After deploying, note the URL and set XTTS_BASE_URL in .env:
    XTTS_BASE_URL=https://<your>--self-hosted-xtts-serve.modal.run
"""

import subprocess

import modal

GPU = "T4"

app = modal.App("self-hosted-xtts")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libsndfile1")
    .pip_install(
        "TTS>=0.22.0",
        "fastapi",
        "uvicorn",
        "numpy",
    )
    # Pre-download the XTTS v2 model during image build
    .run_commands(
        'python -c "'
        "from TTS.api import TTS; "
        "TTS('tts_models/multilingual/multi-dataset/xtts_v2')"
        '"'
    )
    .add_local_file("tts_server.py", "/root/tts_server.py")
)


@app.function(
    image=image,
    gpu=GPU,
    scaledown_window=300,
    timeout=600,
    keep_warm=0,
)
@modal.web_server(port=8001, startup_timeout=120)
def serve():
    subprocess.Popen(
        ["uvicorn", "tts_server:app", "--host", "0.0.0.0", "--port", "8001"]
    )
