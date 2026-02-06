"""Quick script to trigger an outbound call via the /dialout endpoint.

Usage:
    uv run python test_call.py                              # local Docker (default)
    uv run python test_call.py https://your-modal-url       # Modal deployment
    uv run python test_call.py https://your-tunnel-url      # dev tunnel

Phone numbers (TO_NUMBER, FROM_NUMBER) are always read from outbound/.env.
The server URL can be passed as an argument or falls back to LOCAL_SERVER_URL
in .env (default: http://localhost:7860).
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / "outbound" / ".env"
load_dotenv(ENV_PATH, override=True)


def main():
    to_number = os.getenv("TO_NUMBER")
    from_number = os.getenv("FROM_NUMBER")

    # Accept optional server URL as first argument
    if len(sys.argv) > 1:
        server_url = sys.argv[1].rstrip("/")
    else:
        server_url = os.getenv("LOCAL_SERVER_URL", "http://localhost:7860")

    if not to_number or not from_number:
        print("Error: TO_NUMBER and FROM_NUMBER must be set in outbound/.env")
        sys.exit(1)

    url = f"{server_url}/dialout"
    payload = {"to_number": to_number, "from_number": from_number}

    print(f"Calling {to_number} from {from_number}...")
    print(f"POST {url}")

    response = httpx.post(url, json=payload)

    if response.status_code == 200:
        data = response.json()
        print(f"Call initiated! SID: {data['call_sid']}")
    else:
        print(f"Error {response.status_code}: {response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
