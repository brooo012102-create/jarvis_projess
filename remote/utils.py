"""
remote/utils.py — Shared utilities for the remote control system.
"""

import logging
import platform
import socket
import sys
import threading
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ─── Base Directory ─────────────────────────────────────────────────────────

def get_base_dir() -> Path:
    """Project root, works both in dev and frozen (PyInstaller) mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
LOG_DIR = BASE_DIR / "logs"


# ─── Logging Setup ──────────────────────────────────────────────────────────

def setup_logging(name: str = "remote", level: int = logging.INFO) -> logging.Logger:
    """
    Create a logger that writes to both console and a rotating log file.
    Log file: logs/remote.log (max 5 MB, 3 backups)
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler (rotating)
    fh = RotatingFileHandler(
        LOG_DIR / "remote.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ─── Device Info ─────────────────────────────────────────────────────────────

def get_device_name() -> str:
    """Return a human-readable device identifier."""
    try:
        return socket.gethostname()
    except Exception:
        return platform.node() or "Unknown"


def get_local_ip() -> str:
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─── Thread-safe Helpers ────────────────────────────────────────────────────

def safe_thread(target, *, name: str = None, daemon: bool = True, args=(), kwargs=None):
    """
    Start a daemon thread that catches and logs all exceptions
    instead of crashing the process.
    """
    log = logging.getLogger("remote")

    def _wrapper():
        try:
            target(*args, **(kwargs or {}))
        except Exception as exc:
            log.error("Thread '%s' crashed: %s", name or target.__name__, exc, exc_info=True)

    t = threading.Thread(target=_wrapper, name=name, daemon=daemon)
    t.start()
    return t


def timestamp_str() -> str:
    """Current local time as a readable string for notifications."""
    return time.strftime("%d.%m.%Y %H:%M:%S")
