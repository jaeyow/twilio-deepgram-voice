"""Minimal XTTS v2 streaming server compatible with Pipecat's XTTSService.

Implements two endpoints:
  GET  /studio_speakers  — returns pre-computed speaker embeddings
  POST /tts_stream       — streams raw 24kHz PCM audio from text

This matches the API contract of the coqui-ai/xtts-streaming-server
that XTTSService expects.
"""

import io
import os

import numpy as np
import torch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# Globals populated on startup
model = None
studio_speakers = {}


@app.on_event("startup")
def load_model():
    global model, studio_speakers

    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    # TTS downloads models to ~/.local/share/tts/ by default
    model_dir = os.path.expanduser(
        "~/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
    )
    config_path = os.path.join(model_dir, "config.json")
    speakers_path = os.path.join(model_dir, "speakers_xtts.pth")

    config = XttsConfig()
    config.load_json(config_path)

    model = Xtts.init_from_config(config)
    model.load_checkpoint(config, checkpoint_dir=model_dir, use_deepspeed=False)

    # TTS_DEVICE=cuda  — Modal / NVIDIA GPU
    # TTS_DEVICE=mps   — Native Mac (Apple Silicon Metal GPU)
    # TTS_DEVICE=cpu   — Docker on Mac, or any CPU-only machine
    device = os.getenv("TTS_DEVICE", "cuda")
    if device == "cuda" and torch.cuda.is_available():
        model.cuda()
    elif device == "mps" and torch.backends.mps.is_available():
        model.to("mps")
        print("Running XTTS on Apple Silicon GPU (MPS)")
    else:
        print("Running XTTS on CPU (slower, but no GPU required)")

    # Load pre-computed studio speaker embeddings
    if os.path.exists(speakers_path):
        raw_speakers = torch.load(speakers_path, weights_only=True)
        for name, data in raw_speakers.items():
            studio_speakers[name] = {
                "speaker_embedding": data["speaker_embedding"]
                .cpu()
                .squeeze()
                .half()
                .tolist(),
                "gpt_cond_latent": data["gpt_cond_latent"]
                .cpu()
                .squeeze()
                .half()
                .tolist(),
            }

    print(f"XTTS v2 loaded. {len(studio_speakers)} studio speakers available.")


@app.get("/studio_speakers")
async def get_studio_speakers():
    return JSONResponse(content=studio_speakers)


@app.post("/tts_stream")
async def tts_stream(request: Request):
    data = await request.json()

    text = data.get("text", "")
    language = data.get("language", "en")
    speaker_embedding = data.get("speaker_embedding")
    gpt_cond_latent = data.get("gpt_cond_latent")
    stream_chunk_size = data.get("stream_chunk_size", 20)

    if not text or not speaker_embedding or not gpt_cond_latent:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing text, speaker_embedding, or gpt_cond_latent"},
        )

    # Convert lists back to tensors
    speaker_embedding_tensor = (
        torch.tensor(speaker_embedding).unsqueeze(0).unsqueeze(-1).to(model.device)
    )
    gpt_cond_latent_tensor = (
        torch.tensor(gpt_cond_latent).unsqueeze(0).to(model.device)
    )

    def generate():
        chunks = model.inference_stream(
            text,
            language,
            gpt_cond_latent_tensor,
            speaker_embedding_tensor,
            stream_chunk_size=stream_chunk_size,
        )
        for chunk in chunks:
            # Convert float tensor to 16-bit PCM bytes
            audio_np = chunk.cpu().numpy()
            pcm_data = (audio_np * 32767).astype(np.int16).tobytes()
            yield pcm_data

    return StreamingResponse(generate(), media_type="audio/raw")
