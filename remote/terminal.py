"""
remote/terminal.py — Jarvis text chat broker with real-time IPC.

Provides a queue-based interface to exchange messages between the web UI
and the desktop main.py Jarvis instance. Supports WebSocket for real-time
message delivery and proper waiting for Jarvis responses.
"""

import logging
import threading
import time
from collections import deque

log = logging.getLogger("remote.terminal")


class ChatMessage:
    def __init__(self, role: str, text: str):
        self.role = role       # "user" or "jarvis"
        self.text = text
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
        }


class TerminalManager:
    """
    Manages a text chat session, acting as a broker between Web UI and main.py.
    Thread-safe, maintains conversation history and a pending queue.
    Supports waiting for Jarvis responses with configurable timeout.
    """

    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self._history: deque[ChatMessage] = deque(maxlen=max_history)
        self._pending: list[str] = []
        self._response_event = threading.Event()
        self._latest_response: str | None = None
        self._ws_clients: list = []  # WebSocket clients for real-time push
        log.info("Terminal aracı başlatıldı (Masaüstü Jarvis ile iletişim kurulacak)")

    @property
    def history(self) -> list[dict]:
        with self._lock:
            return [m.to_dict() for m in self._history]

    def add_user_message(self, text: str) -> str:
        """Called by Web UI to send a message to Jarvis.
        Waits up to 15 seconds for a response from main.py."""
        text = text.strip()
        if not text:
            return ""

        msg = ChatMessage("user", text)
        with self._lock:
            self._history.append(msg)
            self._pending.append(text)
            self._response_event.clear()
            self._latest_response = None

        log.info("Kullanıcı mesajı kuyruğa eklendi: %s", text[:50])

        # Wait for Jarvis to respond (main.py polls, processes, replies)
        got_response = self._response_event.wait(timeout=15.0)

        if got_response and self._latest_response:
            return self._latest_response
        else:
            return ""  # No response text — frontend will show waiting state

    def add_user_message_async(self, text: str) -> dict:
        """Non-blocking version: queues message and returns immediately.
        Used by the API endpoint to avoid blocking the event loop."""
        text = text.strip()
        if not text:
            return {"queued": False}

        msg = ChatMessage("user", text)
        with self._lock:
            self._history.append(msg)
            self._pending.append(text)
            self._response_event.clear()
            self._latest_response = None

        log.info("Kullanıcı mesajı kuyruğa eklendi (async): %s", text[:50])
        return {"queued": True, "message_id": len(self._history)}

    def add_jarvis_message(self, text: str):
        """Called by main.py to send Jarvis's reply to Web UI."""
        text = text.strip()
        if not text:
            return
        msg = ChatMessage("jarvis", text)
        with self._lock:
            self._history.append(msg)
            self._latest_response = text
        self._response_event.set()
        log.info("Jarvis yanıtı alındı: %s", text[:50])

    def pop_pending(self) -> list[str]:
        """Called by main.py to retrieve pending user messages."""
        with self._lock:
            msgs = list(self._pending)
            self._pending.clear()
            return msgs

    def has_pending(self) -> bool:
        """Check if there are pending messages for main.py."""
        with self._lock:
            return len(self._pending) > 0

    def clear_history(self):
        """Clear conversation history."""
        with self._lock:
            self._history.clear()
            self._pending.clear()
            self._latest_response = None
        self._response_event.clear()
        log.info("Terminal geçmişi temizlendi.")
