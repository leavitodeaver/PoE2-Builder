"""Usage: uv run python -m builder.web [--port 8765] [--no-browser]

Without --port, the first free port from 8765 is used; if this version of the page is already
running there, the browser just opens it (so double-clicking the launcher twice is harmless).
"""

from __future__ import annotations

import argparse
import socket
import threading
import urllib.request
import webbrowser

import uvicorn

FIRST_PORT = 8765


def serving_this_version(port: int) -> bool:
    """Is a current PoE2 Builder (one with optimizer runs) answering on this port?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs/current", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PoE2 Builder web page locally.")
    parser.add_argument("--port", type=int, help=f"default: the first free port from {FIRST_PORT}")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = args.port
    if port is None:
        for candidate in range(FIRST_PORT, FIRST_PORT + 20):
            if serving_this_version(candidate):
                url = f"http://127.0.0.1:{candidate}/"
                print(f"PoE2 Builder is already running at {url}; opening it.")
                if not args.no_browser:
                    webbrowser.open(url)
                return
            if port_free(candidate):
                port = candidate
                break
        else:
            raise SystemExit(f"No free port between {FIRST_PORT} and {FIRST_PORT + 19}.")

    url = f"http://127.0.0.1:{port}/"
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"PoE2 Builder is running at {url}")
    print("Keep this window open while you use it. Close it to stop.")
    uvicorn.run("builder.web.app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
