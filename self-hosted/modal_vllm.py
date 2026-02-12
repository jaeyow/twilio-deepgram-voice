"""Modal deployment for vLLM serving Llama 3.1 8B Instruct.

Exposes an OpenAI-compatible API at /v1 that the bot connects to
via OLLamaLLMService (which extends OpenAILLMService).

Usage:
    modal deploy modal_vllm.py   # permanent URL
    modal serve modal_vllm.py    # dev mode (temporary URL)

After deploying, note the URL and set VLLM_BASE_URL in .env:
    VLLM_BASE_URL=https://<your>--self-hosted-vllm-serve.modal.run/v1
"""

import subprocess

import modal

MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
GPU = "A10G"

app = modal.App("self-hosted-vllm")

model_volume = modal.Volume.from_name("vllm-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm")
)


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/models": model_volume},
    secrets=[modal.Secret.from_dotenv(__file__)],
    scaledown_window=300,
    timeout=600,
    keep_warm=0,
)
@modal.web_server(port=8000, startup_timeout=300)
def serve():
    cmd = [
        "vllm", "serve", MODEL,
        "--host", "0.0.0.0",
        "--port", "8000",
        "--dtype", "auto",
        "--max-model-len", "8192",
        "--download-dir", "/models",
        "--enforce-eager",
    ]
    subprocess.Popen(cmd)
