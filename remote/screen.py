"""
remote/screen.py - High-performance live screen capture and streaming.

Uses mss for fast screen capture, PIL for JPEG compression,
provides WebSocket (primary) and MJPEG (fallback) streaming.
Optimized for 60 FPS with shared frame buffer architecture.
"""

import io
import logging
import threading
import time
from typing import Tuple, Optional

log = logging.getLogger("remote.screen")


class ScreenStreamer:
    """
    High-performance screen streamer with shared frame buffer.
    A background thread continuously captures the screen and stores
    the latest JPEG frame. WebSocket/MJPEG clients read from this buffer
    instead of capturing individually.
    """

    def __init__(self):
        self.fps = 30
        self.quality = 45
        self.scale = 0.5

        self._lock = threading.Lock()
        self._client_count = 0

        # Shared frame buffer
        self._frame_lock = threading.Lock()
        self._latest_frame = b""
        self._frame_event = threading.Event()
        self._frame_id = 0

        # Capture thread
        self._capture_thread = None
        self._capture_running = False

    def update_config(self, fps=None, quality=None, scale=None):
        """Update streaming parameters."""
        if fps is not None:
            self.fps = max(1, min(60, fps))
        if quality is not None:
            self.quality = max(10, min(100, quality))
        if scale is not None:
            self.scale = max(0.1, min(1.0, scale))
        log.info(
            "Ekran ayarlari guncellendi: fps=%d, quality=%d, scale=%.1f",
            self.fps, self.quality, self.scale,
        )

    def get_config(self):
        return {"fps": self.fps, "quality": self.quality, "scale": self.scale}

    def _start_capture_thread(self):
        """Start the background capture thread if not already running."""
        if self._capture_running:
            return
        self._capture_running = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="screen-capture", daemon=True
        )
        self._capture_thread.start()
        log.info("Ekran yakalama thread'i baslatildi.")

    def _stop_capture_thread(self):
        """Stop the background capture thread if no clients remain."""
        with self._lock:
            if self._client_count > 0:
                return
        self._capture_running = False
        log.info("Ekran yakalama thread'i durduruldu.")

    def _capture_loop(self):
        """
        Continuously captures the screen at target FPS.
        Uses a persistent mss context for maximum performance.
        """
        import mss
        from PIL import Image, ImageDraw
        import ctypes
        import ctypes.wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.DWORD),
                ("flags", ctypes.wintypes.DWORD),
                ("hCursor", ctypes.c_void_p),
                ("ptScreenPos", POINT),
            ]

        def get_cursor_info():
            info = CURSORINFO()
            info.cbSize = ctypes.sizeof(CURSORINFO)
            try:
                if ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)):
                    if info.flags == 1:
                        return True, info.ptScreenPos.x, info.ptScreenPos.y
            except Exception:
                pass
            return False, 0, 0

        with mss.mss() as sct:
            while self._capture_running:
                frame_start = time.monotonic()
                target_fps = self.fps
                current_quality = self.quality
                current_scale = self.scale

                try:
                    monitor = sct.monitors[1]
                    img = sct.grab(monitor)

                    pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

                    if current_scale < 1.0:
                        new_w = int(pil_img.width * current_scale)
                        new_h = int(pil_img.height * current_scale)
                        pil_img = pil_img.resize((new_w, new_h), Image.BILINEAR)

                    visible, cx, cy = get_cursor_info()
                    if visible:
                        local_x = int((cx - monitor["left"]) * current_scale)
                        local_y = int((cy - monitor["top"]) * current_scale)
                        draw = ImageDraw.Draw(pil_img)
                        s = max(1, int(8 * current_scale))
                        points = [
                            (local_x, local_y),
                            (local_x + s, local_y + s),
                            (local_x + s // 2, local_y + s),
                            (local_x + s // 2, local_y + int(s * 1.5)),
                            (local_x, local_y + int(s * 1.2)),
                        ]
                        draw.polygon(points, fill="white", outline="black")

                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=current_quality)
                    frame_data = buf.getvalue()

                    with self._frame_lock:
                        self._latest_frame = frame_data
                        self._frame_id += 1
                    self._frame_event.set()
                    self._frame_event.clear()

                except Exception as exc:
                    log.error("Kare yakalama hatasi: %s", exc)
                    time.sleep(0.1)
                    continue

                elapsed = time.monotonic() - frame_start
                target_delay = 1.0 / target_fps
                sleep_time = target_delay - elapsed
                if sleep_time > 0.001:
                    time.sleep(sleep_time)

    def get_latest_frame(self) -> Tuple[bytes, int]:
        """Get the latest captured frame and its ID."""
        with self._frame_lock:
            return self._latest_frame, self._frame_id

    def capture_single(self) -> bytes:
        """Capture a single frame as JPEG bytes (for snapshot endpoint)."""
        import mss
        from PIL import Image

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

            if self.scale < 1.0:
                new_w = int(pil_img.width * self.scale)
                new_h = int(pil_img.height * self.scale)
                pil_img = pil_img.resize((new_w, new_h), Image.BILINEAR)

            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=self.quality)
            return buf.getvalue()

    def register_client(self):
        """Register a new streaming client and start capture if needed."""
        with self._lock:
            self._client_count += 1
            count = self._client_count
        log.info("Ekran istemcisi baglandi (toplam: %d)", count)
        self._start_capture_thread()

    def unregister_client(self):
        """Unregister a streaming client and stop capture if no clients."""
        with self._lock:
            self._client_count = max(0, self._client_count - 1)
            count = self._client_count
        log.info("Ekran istemcisi ayrildi (kalan: %d)", count)
        if count == 0:
            self._stop_capture_thread()

    def mjpeg_generator(self):
        """
        Generator that yields MJPEG multipart frames.
        Reads from the shared frame buffer for performance.
        """
        self.register_client()

        last_frame_id = -1
        try:
            while self._capture_running:
                self._frame_event.wait(timeout=0.1)

                frame_data, frame_id = self.get_latest_frame()
                if not frame_data or frame_id == last_frame_id:
                    continue
                last_frame_id = frame_id

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame_data)).encode() + b"\r\n"
                    b"\r\n" + frame_data + b"\r\n"
                )
        finally:
            self.unregister_client()
