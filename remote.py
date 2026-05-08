"""
remote.py — Entry point for the Jarvis Remote Control System.

Orchestrates:
  1. Configuration loading
  2. ntfy notification client
  3. Auth manager (token + OTP)
  4. MAC security scanner
  5. FastAPI server (uvicorn)
  6. Cloudflare tunnel (auto-restart)

Usage:
    python remote.py
"""

import json
import signal
import sys
import threading
import time
from pathlib import Path

# ─── Ensure project root is on sys.path ─────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from remote.utils import setup_logging, get_device_name, get_local_ip
from remote.ntfy import NtfyClient
from remote.auth import AuthManager
from remote.security import MacScanner, EmergencyLock
from remote.cloudflare import TunnelManager
from remote.audio import AudioController
from remote.screen import ScreenStreamer
from remote.terminal import TerminalManager
from remote.server import create_app
from remote.tray import TrayIcon

# ─── Logging ─────────────────────────────────────────────────────────────────
log = setup_logging("remote")

# ─── Configuration ───────────────────────────────────────────────────────────
CONFIG_PATH = PROJECT_ROOT / "config" / "remote_config.json"


def load_config() -> dict:
    """Load remote_config.json with defaults."""
    defaults = {
        "ntfy_topic": "jarvis-remote-CHANGEME",
        "ntfy_server": "https://ntfy.sh",
        "trusted_mac": "",
        "mac_scan_interval": 60,
        "server_port": 8765,
        "screen_fps": 15,
        "screen_quality": 50,
        "screen_scale": 0.5,
    }

    if not CONFIG_PATH.exists():
        log.warning("Yapılandırma bulunamadı: %s — varsayılanlar kullanılıyor.", CONFIG_PATH)
        return defaults

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        # Merge with defaults
        return {**defaults, **user_cfg}
    except Exception as exc:
        log.error("Yapılandırma okunamadı: %s", exc)
        return defaults


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    port = config["server_port"]

    log.info("=" * 60)
    log.info("  JARVIS UZAKTAN KONTROL SISTEMI v1.0")
    log.info("=" * 60)
    log.info("Cihaz     : %s", get_device_name())
    log.info("Yerel IP  : %s", get_local_ip())
    log.info("Port      : %d", port)
    log.info("ntfy konu : %s", config.get("ntfy_topic", "(yok)"))
    log.info("=" * 60)

    # 1 ── ntfy holder (mutable dict so server.py can access updated client)
    ntfy_holder = {"client": None}

    ntfy_topic = config.get("ntfy_topic", "")
    if ntfy_topic:
        ntfy_holder["client"] = NtfyClient(
            topic=ntfy_topic,
            server=config["ntfy_server"],
        )
    else:
        log.warning("ntfy kanal adi yok — web kurulum sayfasindan girilebilir.")

    # 2 ── Auth manager
    auth = AuthManager()
    token = auth.session_token
    log.info("Oturum anahtarı oluşturuldu: %s…", token[:16])

    # 3 ── Audio controller
    audio = AudioController()

    # 4 ── Screen streamer
    screen = ScreenStreamer()

    # 5 ── Terminal (Jarvis chat broker)
    terminal = TerminalManager()

    # 5.5 ── Emergency Lock
    emergency_lock = EmergencyLock()

    # 6 ── MAC scanner (Early init for notifications)
    mac_scanner = MacScanner(
        trusted_mac=config.get("trusted_mac", ""),
        ntfy_client=ntfy_holder["client"],
        interval=config.get("mac_scan_interval", 60),
    )

    # 7 ── Tunnel manager
    tunnel = TunnelManager(port=port)

    def on_tunnel_ready(url: str):
        """Called when tunnel URL is available."""
        log.info("Tunel hazir: %s", url)
        
        # Ek ağ kararlılığı için kısa bir bekleme
        time.sleep(3)
        
        # Re-generate auth on every new tunnel
        new_token = auth.regenerate_token()
        auth.invalidate_all_sessions()
        
        # Send notifications every time the tunnel starts
        _ntfy = ntfy_holder["client"]
        if _ntfy:
            _ntfy.send_url(url)
            _ntfy.send_token(new_token)
        else:
            log.warning("ntfy client yok — bildirim gonderilemedi.")
        log.info("Yeni oturum anahtari gonderildi.")

    tunnel.on_url_ready = on_tunnel_ready

    # 8 ── FastAPI app
    app = create_app(
        auth=auth,
        audio=audio,
        screen=screen,
        emergency_lock=emergency_lock,
        terminal=terminal,
        ntfy_holder=ntfy_holder,
        tunnel_url_getter=lambda: tunnel.current_url,
    )

    # ─── Cleanup task (every 5 min) ──────────────────────────────
    cleanup_stop = threading.Event()

    def _cleanup_loop():
        while not cleanup_stop.wait(timeout=300):
            auth.cleanup_expired()
            log.debug("Süresi dolmuş oturumlar temizlendi.")

    cleanup_thread = threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True)
    cleanup_thread.start()

    # ─── Start services ─────────────────────────────────────────
    mac_scanner.start()

    def start_tunnel_delayed():
        log.info("Sunucunun tamamen hazir olmasi icin 5 saniye bekleniyor...")
        time.sleep(5)
        tunnel.start()

    threading.Thread(target=start_tunnel_delayed, name="tunnel-starter", daemon=True).start()

    # ─── Graceful shutdown ──────────────────────────────────────
    def shutdown(signum=None, frame=None):
        log.info("Sistem kapatiliyor...")
        if 'tray' in locals():
            tray.stop()
        tunnel.stop()
        mac_scanner.stop()
        cleanup_stop.set()
        log.info("Gule gule!")
        import os
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ─── Tray Icon ──────────────────────────────────────────────
    def resend_notification():
        if tunnel.current_url:
            on_tunnel_ready(tunnel.current_url)
        elif ntfy_holder["client"] is None:
            log.warning("ntfy client yok — once kurulumu tamamlayin.")
        else:
            log.warning("Tunel URL henüz hazir degil, bildirim gonderilemiyor.")

    tray = TrayIcon(
        on_resend_click=resend_notification,
        on_exit_click=shutdown
    )
    tray.start()

    # ─── Start uvicorn (blocking) ───────────────────────────────
    import uvicorn

    log.info("FastAPI sunucusu baslatiliyor -> http://127.0.0.1:%d", port)

    # Custom uvicorn log config to avoid crash on pythonw
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
            },
        },
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "logs/remote.log",
                "mode": "a",
                "encoding": "utf-8",
                "maxBytes": 5242880,
                "backupCount": 3,
                "formatter": "default",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO"},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }

    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=True,
            log_config=log_config,
        )
    except Exception as exc:
        log.error("FastAPI sunucusu beklenmedik bir hata ile durdu: %s", exc, exc_info=True)


if __name__ == "__main__":
    main()
