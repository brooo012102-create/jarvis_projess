"""
Cloudflare Tunnel Manager
=========================
Starts a cloudflared quick tunnel, extracts the generated public URL,
and pushes it to an ntfy topic for remote access.

Usage:
    python tunnel.py --port 5000 --ntfy-topic my-secret-topic
"""

import subprocess
import re
import time
import sys
import argparse
import logging
import requests
import threading
import signal

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tunnel")

# ─── Constants ──────────────────────────────────────────────────────────────
NTFY_BASE_URL = "https://ntfy.sh"
URL_PATTERN = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
CLOUDFLARED_BIN = "cloudflared"  # Must be on PATH or provide full path


# ─── Argument Parser ───────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a Cloudflare Tunnel and notify via ntfy."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Local port to expose through the tunnel (default: 5000)",
    )
    parser.add_argument(
        "--ntfy-topic",
        required=True,
        help="ntfy.sh topic name to send the tunnel URL to",
    )
    parser.add_argument(
        "--ntfy-server",
        default=NTFY_BASE_URL,
        help=f"Custom ntfy server URL (default: {NTFY_BASE_URL})",
    )
    parser.add_argument(
        "--title",
        default="🔗 Tunnel Ready",
        help="Notification title (default: '🔗 Tunnel Ready')",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Seconds to wait for the tunnel URL before giving up (default: 30)",
    )
    parser.add_argument(
        "--protocol",
        choices=["http", "https"],
        default="http",
        help="Protocol of the local service (default: http)",
    )
    return parser.parse_args()


# ─── Core Functions ─────────────────────────────────────────────────────────
def start_tunnel(port: int, protocol: str = "http") -> subprocess.Popen:
    """Launch cloudflared quick tunnel as a subprocess."""
    cmd = [
        CLOUDFLARED_BIN,
        "tunnel",
        "--url",
        f"{protocol}://localhost:{port}",
    ]
    log.info("Starting tunnel: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # cloudflared writes to stderr
        text=True,
        bufsize=1,  # line-buffered
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return process


def extract_url(process: subprocess.Popen, timeout: int = 30) -> str | None:
    """
    Read cloudflared output line-by-line and return the first
    trycloudflare.com URL found, or None on timeout.
    """
    url_found = None
    deadline = time.monotonic() + timeout

    def _reader():
        nonlocal url_found
        for line in process.stdout:
            line = line.strip()
            if line:
                log.debug("cloudflared │ %s", line)
            match = URL_PATTERN.search(line)
            if match and url_found is None:
                url_found = match.group(0)
                return

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    while url_found is None and time.monotonic() < deadline:
        if process.poll() is not None:
            log.error("cloudflared exited early with code %d", process.returncode)
            return None
        time.sleep(0.25)

    if url_found:
        log.info("Tunnel URL captured: %s", url_found)
    else:
        log.error("Timed out after %ds waiting for tunnel URL", timeout)

    return url_found


def send_ntfy(
    url: str,
    topic: str,
    server: str = NTFY_BASE_URL,
    title: str = "🔗 Tunnel Ready",
) -> bool:
    """Send the tunnel URL to an ntfy topic. Returns True on success."""
    endpoint = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Tags": "rocket,link",
        "Priority": "high",
        "Click": url,
    }
    body = f"Your Cloudflare Tunnel is live!\n\n{url}"

    try:
        resp = requests.post(endpoint, data=body.encode("utf-8"), headers=headers, timeout=10)
        resp.raise_for_status()
        log.info("Notification sent to %s (HTTP %d)", endpoint, resp.status_code)
        return True
    except requests.RequestException as exc:
        log.error("Failed to send notification: %s", exc)
        return False


def keep_alive(process: subprocess.Popen) -> None:
    """Block until cloudflared exits or the user sends Ctrl+C."""
    log.info("Tunnel is running. Press Ctrl+C to stop.")
    try:
        process.wait()
    except KeyboardInterrupt:
        log.info("Shutting down tunnel…")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log.info("Tunnel stopped.")


# ─── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()

    # 1 ── Start tunnel
    process = start_tunnel(args.port, args.protocol)

    # Ensure cleanup on exit
    def _cleanup(signum, frame):
        process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    # 2 ── Extract URL
    tunnel_url = extract_url(process, timeout=args.timeout)
    if tunnel_url is None:
        process.terminate()
        sys.exit(1)

    # 3 ── Notify
    send_ntfy(
        url=tunnel_url,
        topic=args.ntfy_topic,
        server=args.ntfy_server,
        title=args.title,
    )

    # 4 ── Stay alive
    keep_alive(process)


if __name__ == "__main__":
    main()
