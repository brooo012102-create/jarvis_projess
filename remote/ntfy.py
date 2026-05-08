"""
remote/ntfy.py — Centralized push notification client via ntfy.sh.

All system events (tunnel URL, OTP codes, security alerts, status updates)
are routed through this single module.
"""

import logging
import time
import requests

from remote.utils import get_device_name, timestamp_str

log = logging.getLogger("remote.ntfy")


class NtfyClient:
    """Thread-safe ntfy.sh notification sender with retry logic."""

    def __init__(self, topic: str, server: str = "https://ntfy.sh"):
        self.topic = topic
        self.server = server.rstrip("/")
        self.endpoint = f"{self.server}/{self.topic}"
        self.device = get_device_name()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Jarvis-Remote/1.0"})
        log.info("NtfyClient hazir -> %s", self.endpoint)

    # ─── Core Send ──────────────────────────────────────────────────────

    def send(
        self,
        message: str,
        *,
        title: str = "Jarvis",
        priority: str = "default",
        tags: list[str] | None = None,
        click_url: str | None = None,
        max_retries: int = 12,
        append_meta: bool = True,
    ) -> bool:
        """
        Send a notification. Returns True on success.
        Retries up to max_retries with exponential backoff.
        Never raises — failures are logged and swallowed.
        """
        headers = {
            "Title": title,
            "Priority": priority,
            "Tags": ",".join(tags or []),
        }
        if click_url:
            headers["Click"] = click_url

        if append_meta:
            from remote.utils import get_device_name, get_local_ip
            body = f"{message}\n\nCihaz: {get_device_name()}\nIP: {get_local_ip()}"
        else:
            body = message

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._session.post(
                    self.endpoint,
                    data=body.encode("utf-8"),
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                log.info("Bildirim gönderildi (%s) [HTTP %d]", title, resp.status_code)
                return True
            except requests.RequestException as exc:
                log.warning(
                    "Bildirim başarısız (deneme %d/%d): %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    time.sleep(2 ** attempt)  # 2, 4, 8 seconds

        log.error("Bildirim gönderilemedi: %s", title)
        return False

    # ─── Convenience Methods ────────────────────────────────────────────

    def send_url(self, url: str) -> bool:
        """Send the tunnel URL notification."""
        return self.send(
            f"🔗 Tünel Hazır\n🌐 Uzaktan erişim aktif!\n\n{url}",
            title="Tunel Hazir",
            priority="high",
            tags=["rocket", "link"],
            click_url=url,
        )

    def send_token(self, token: str) -> bool:
        """Send the session token for first-phase auth."""
        return self.send(
            token,
            title="Giris Anahtari",
            priority="high",
            tags=["key", "lock"],
            append_meta=False,
        )

    def send_otp(self, otp: str) -> bool:
        """Send OTP code for second-phase auth."""
        return self.send(
            otp,
            title="OTP Dogrulama",
            priority="urgent",
            tags=["rotating_light", "lock"],
            append_meta=False,
        )

    def send_alert(self, message: str) -> bool:
        """Send a high-priority security alert."""
        return self.send(
            f"🚨 Güvenlik Uyarısı\n⚠️ {message}",
            title="Guvenlik Uyarisi",
            priority="urgent",
            tags=["warning", "rotating_light"],
        )

    def send_status(self, message: str) -> bool:
        """Send a system status update."""
        return self.send(
            f"ℹ️ Sistem Durumu\n{message}",
            title="Sistem Durumu",
            priority="low",
            tags=["information_source"],
        )
