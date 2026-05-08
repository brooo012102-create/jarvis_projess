"""
remote/cloudflare.py — Cloudflare Tunnel lifecycle manager.

Starts cloudflared as a subprocess, extracts the generated URL,
auto-restarts on crash with exponential backoff.
"""

import logging
import re
import subprocess
import sys
import threading
import time
import os
import winreg

import shutil

log = logging.getLogger("remote.cloudflare")

URL_PATTERN = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")

def _get_cloudflared_path():
    # Check PATH first
    cmd = shutil.which("cloudflared")
    if cmd:
        return cmd
    # Check default winget path
    winget_path = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe")
    if os.path.exists(winget_path):
        return winget_path
    return "cloudflared"

CLOUDFLARED_BIN = _get_cloudflared_path()


class TunnelManager:
    """
    Manages a cloudflared quick-tunnel subprocess.

    Usage:
        tm = TunnelManager(port=8765, on_url_ready=callback)
        tm.start()
        ...
        tm.stop()
    """

    def __init__(
        self,
        port: int = 8765,
        protocol: str = "http",
        on_url_ready=None,
        max_retries: int = 10,
        url_timeout: int = 30,
    ):
        self.port = port
        self.protocol = protocol
        self.on_url_ready = on_url_ready  # callback(url: str)
        self.max_retries = max_retries
        self.url_timeout = url_timeout

        self._process: subprocess.Popen | None = None
        self._current_url: str | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._retry_count = 0

    # ─── Properties ─────────────────────────────────────────────────────

    @property
    def current_url(self) -> str | None:
        with self._lock:
            return self._current_url

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ─── Public API ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start tunnel in a background thread."""
        self._stop_event.clear()
        self._retry_count = 0
        t = threading.Thread(target=self._run_loop, name="tunnel-manager", daemon=True)
        t.start()
        log.info("Tünel yöneticisi başlatıldı (port=%d)", self.port)

    def stop(self) -> None:
        """Gracefully stop the tunnel."""
        self._stop_event.set()
        self._kill_process()
        log.info("Tünel durduruldu.")

    # ─── Internal ───────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main loop: start tunnel, watch for crash, restart."""
        while not self._stop_event.is_set():
            if self._retry_count >= self.max_retries:
                log.error(
                    "Maksimum yeniden deneme sayısına ulaşıldı (%d). Tünel durduruluyor.",
                    self.max_retries,
                )
                return

            try:
                self._launch()
                url = self._wait_for_url()

                if url:
                    with self._lock:
                        self._current_url = url
                    self._retry_count = 0  # success resets counter
                    log.info("Tünel URL: %s", url)

                    if self.on_url_ready:
                        try:
                            self.on_url_ready(url)
                        except Exception as exc:
                            log.error("on_url_ready callback hatası: %s", exc)

                    # Keep alive — block until process dies
                    self._process.wait()

                    if self._stop_event.is_set():
                        return

                    log.warning("cloudflared beklenmedik şekilde kapandı (code=%s)", self._process.returncode)
                else:
                    log.warning("URL %d saniye icinde bulunamadi, yeniden deneniyor...", self.url_timeout)
                    self._kill_process()

            except Exception as exc:
                log.error("Tunel hatasi: %s", exc, exc_info=True)
                self._kill_process()

            # Exponential backoff: 2, 4, 8, 16, 32... capped at 60s
            self._retry_count += 1
            delay = min(60, 2 ** self._retry_count)
            log.info("Yeniden baslatma %d/%d - %ds bekleniyor...", self._retry_count, self.max_retries, delay)

            # Wait with stop-event awareness
            if self._stop_event.wait(timeout=delay):
                return

    def _launch(self) -> None:
        """Start the cloudflared subprocess."""
        cmd = [
            CLOUDFLARED_BIN,
            "tunnel",
            "--url",
            f"{self.protocol}://127.0.0.1:{self.port}",
        ]
        log.info("cloudflared baslatiliyor: %s", " ".join(cmd))

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
            
        # Update PATH from registry so we can find cloudflared after winget install
        env = os.environ.copy()
        if sys.platform == "win32":
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"System\CurrentControlSet\Control\Session Manager\Environment") as key:
                    sys_path = winreg.QueryValueEx(key, "Path")[0]
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                    user_path = winreg.QueryValueEx(key, "Path")[0]
                env["PATH"] = sys_path + ";" + user_path + ";" + env.get("PATH", "")
            except Exception:
                pass

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
            env=env
        )

    def _wait_for_url(self) -> str | None:
        """
        Read process output in a thread, return URL or None on timeout.
        """
        url_found: list[str] = []  # mutable container for thread

        def _reader():
            try:
                for line in self._process.stdout:
                    line = line.strip()
                    if line:
                        log.debug("cloudflared │ %s", line)
                    match = URL_PATTERN.search(line)
                    if match and not url_found:
                        url_found.append(match.group(0))
                        return
            except Exception:
                pass

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        deadline = time.monotonic() + self.url_timeout
        while not url_found and time.monotonic() < deadline:
            if self._process.poll() is not None:
                log.error("cloudflared erken kapandı (code=%d)", self._process.returncode)
                return None
            if self._stop_event.is_set():
                return None
            time.sleep(0.25)

        return url_found[0] if url_found else None

    def _kill_process(self) -> None:
        """Terminate/kill the subprocess."""
        if self._process is None:
            return
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        except Exception:
            pass
        finally:
            self._process = None
