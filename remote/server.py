"""
remote/server.py — FastAPI backend for the remote control system.

Routes:
  GET  /               → Login page
  GET  /dashboard      → Dashboard (requires session)
  POST /api/auth/token → Verify session token
  POST /api/auth/otp   → Verify OTP
  GET  /api/auth/status→ Check session
  GET  /api/audio/volume    → Get volume
  POST /api/audio/volume    → Set volume
  POST /api/audio/mute      → Toggle mute
  GET  /api/screen/stream   → MJPEG stream
  POST /api/screen/config   → Update stream config
  GET  /api/system/status   → System info
  POST /api/terminal/send   → Send message to Jarvis
  GET  /api/terminal/history→ Get chat history
  POST /api/terminal/clear  → Clear chat history
  POST /api/ui/launch       → Launch desktop UI
  POST /api/autostart/install   → Install autostart
  POST /api/autostart/uninstall → Remove autostart
  GET  /api/autostart/status    → Check autostart status
"""

import logging
import subprocess
import sys
import time
import psutil
import asyncio

from fastapi import FastAPI, Request, Response, Cookie, HTTPException, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from remote.auth import AuthManager
from remote.audio import AudioController
from remote.screen import ScreenStreamer
from remote.terminal import TerminalManager
from remote.security import EmergencyLock

log = logging.getLogger("remote.server")

# ─── Pydantic Models ────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    token: str

class OTPRequest(BaseModel):
    otp: str

class VolumeRequest(BaseModel):
    level: int

class MuteRequest(BaseModel):
    mute: bool

class ScreenConfigRequest(BaseModel):
    fps: int | None = None
    quality: int | None = None
    scale: float | None = None

class TerminalMessage(BaseModel):
    message: str

# ─── App Factory ─────────────────────────────────────────────────────────────

_start_time = time.time()

def create_app(
    auth: AuthManager,
    audio: AudioController,
    screen: ScreenStreamer,
    emergency_lock: EmergencyLock,
    terminal: TerminalManager = None,
    ntfy_holder: dict | None = None,
    tunnel_url_getter=None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Jarvis Remote",
        docs_url=None,    # disable swagger
        redoc_url=None,   # disable redoc
        openapi_url=None, # disable openapi.json
    )

    web_dir = Path(__file__).resolve().parent.parent / "web"

    # ─── Middleware: Request logging ────────────────────────────────────

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        elapsed = (time.monotonic() - start) * 1000
        log.debug(
            "%s %s → %d (%.0fms)",
            request.method, request.url.path, response.status_code, elapsed,
        )
        return response

    # ─── Helper ────────────────────────────────────────────────────────

    def get_client_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def require_session(request: Request) -> bool:
        session_id = request.cookies.get("session_id", "")
        return auth.validate_session(session_id)

    # ─── Pages ─────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def login_page(request: Request):
        log.info("Login sayfasi talep edildi. IP: %s", get_client_ip(request))
        index = web_dir / "index.html"
        if not index.exists():
            log.error("index.html bulunamadi! Yol: %s", index)
            raise HTTPException(500, "index.html bulunamadı")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page(request: Request):
        if not require_session(request):
            return RedirectResponse("/", status_code=302)
        dash = web_dir / "dashboard.html"
        if not dash.exists():
            raise HTTPException(500, "dashboard.html bulunamadı")
        return HTMLResponse(dash.read_text(encoding="utf-8"))

    # ─── Static Files ──────────────────────────────────────────────

    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    # ─── Auth Endpoints ────────────────────────────────────────────────

    @app.post("/api/auth/token")
    async def verify_token(body: TokenRequest, request: Request):
        ip = get_client_ip(request)
        if auth.verify_token(body.token.strip(), ip):
            # Generate and send OTP
            otp = auth.generate_otp()
            _ntfy = ntfy_holder.get("client") if ntfy_holder else None
            if _ntfy:
                _ntfy.send_otp(otp)
            return {"success": True, "message": "Doğrulama kodu gönderildi."}
        return JSONResponse(
            {"success": False, "message": "Geçersiz anahtar."},
            status_code=401,
        )

    @app.post("/api/auth/otp")
    async def verify_otp(body: OTPRequest, request: Request, response: Response):
        ip = get_client_ip(request)

        if not auth.is_token_verified(ip):
            return JSONResponse(
                {"success": False, "message": "Önce oturum anahtarını girin."},
                status_code=401,
            )

        if auth.verify_otp(body.otp.strip(), ip):
            session_id = auth.create_session(ip)
            response = JSONResponse({"success": True, "message": "Giriş başarılı!"})
            response.set_cookie(
                key="session_id",
                value=session_id,
                httponly=True,
                secure=True,
                samesite="strict",
                max_age=3600,
            )
            return response
        return JSONResponse(
            {"success": False, "message": "Geçersiz veya süresi dolmuş kod."},
            status_code=401,
        )

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        valid = require_session(request)
        return {"authenticated": valid}

    # ─── Audio Endpoints ───────────────────────────────────────────────

    @app.get("/api/audio/volume")
    async def get_volume(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        return {
            "volume": audio.get_volume(),
            "muted": audio.get_mute(),
        }

    @app.post("/api/audio/volume")
    async def set_volume(body: VolumeRequest, request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        success = audio.set_volume(body.level)
        return {"success": success, "volume": body.level}

    @app.post("/api/audio/mute")
    async def set_mute(body: MuteRequest, request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        success = audio.set_mute(body.mute)
        return {"success": success, "muted": body.mute}

    # ─── Screen Endpoints ──────────────────────────────────────────────

    @app.get("/api/screen/stream")
    async def screen_stream(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        return StreamingResponse(
            screen.mjpeg_generator(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.post("/api/screen/config")
    async def screen_config(body: ScreenConfigRequest, request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        screen.update_config(fps=body.fps, quality=body.quality, scale=body.scale)
        return {"success": True, **screen.get_config()}

    @app.get("/api/screen/snapshot")
    async def screen_snapshot(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        frame = screen.capture_single()
        return Response(content=frame, media_type="image/jpeg")

    @app.websocket("/api/screen/ws")
    async def screen_ws(websocket: WebSocket):
        await websocket.accept()
        session_id = websocket.cookies.get("session_id")
        if not session_id or not auth.validate_session(session_id):
            await websocket.close(code=1008)
            return

        screen.register_client()
        last_frame_id = -1
        try:
            while True:
                frame_data, frame_id = screen.get_latest_frame()
                if frame_data and frame_id != last_frame_id:
                    await websocket.send_bytes(frame_data)
                    last_frame_id = frame_id
                # Sleep slightly less than frame interval for responsiveness
                target_delay = 1.0 / max(screen.fps, 1)
                await asyncio.sleep(target_delay * 0.8)
        except Exception:
            pass
        finally:
            screen.unregister_client()
            try:
                await websocket.close()
            except Exception:
                pass

    # ─── System Info ───────────────────────────────────────────────────

    @app.get("/api/system/status")
    async def system_status(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")

        uptime_sec = int(time.time() - _start_time)
        hours, remainder = divmod(uptime_sec, 3600)
        minutes, seconds = divmod(remainder, 60)

        return {
            "cpu": psutil.cpu_percent(interval=0),
            "memory": psutil.virtual_memory().percent,
            "uptime": f"{hours}s {minutes}dk {seconds}sn",
            "tunnel_url": tunnel_url_getter() if tunnel_url_getter else None,
        }

    # ─── Terminal Endpoints ─────────────────────────────────────────────

    @app.post("/api/terminal/send")
    async def terminal_send(body: TerminalMessage, request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        if terminal is None:
            raise HTTPException(503, "Terminal devre disi")

        # Queue the message (non-blocking)
        result = terminal.add_user_message_async(body.message)
        if not result.get("queued"):
            return {"response": "", "status": "empty"}

        # Wait for Jarvis response in a thread (up to 15s)
        response_text = await asyncio.to_thread(
            terminal._response_event.wait, 15.0
        )

        if response_text and terminal._latest_response:
            return {
                "response": terminal._latest_response,
                "status": "answered"
            }
        else:
            return {
                "response": "",
                "status": "pending"
            }

    @app.get("/api/terminal/history")
    async def terminal_history(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        if terminal is None:
            return {"history": []}
        return {"history": terminal.history}

    @app.post("/api/terminal/clear")
    async def terminal_clear(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        if terminal:
            terminal.clear_history()
        return {"success": True}

    # ─── Internal IPC (main.py <-> remote.py) ───────────────────────────

    @app.get("/api/internal/terminal/poll")
    async def internal_terminal_poll():
        if terminal is None:
            return {"messages": []}
        msgs = terminal.pop_pending()
        return {"messages": msgs}

    @app.post("/api/internal/terminal/reply")
    async def internal_terminal_reply(request: Request):
        if terminal:
            data = await request.json()
            text = data.get("message", "")
            if text:
                terminal.add_jarvis_message(text)
        return {"success": True}

    # ─── UI Launch Endpoint ────────────────────────────────────────────

    @app.post("/api/ui/launch")
    async def launch_ui(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        try:
            project_root = Path(__file__).resolve().parent.parent
            main_py = project_root / "main.py"
            if not main_py.exists():
                raise HTTPException(404, "main.py bulunamadi")

            # Use python.exe (not pythonw) and no CREATE_NO_WINDOW
            # so the GUI (PyQt/Tkinter) window actually appears
            python_exe = sys.executable
            if python_exe.lower().endswith("pythonw.exe"):
                python_exe = python_exe[:-5] + ".exe"  # pythonw -> python

            subprocess.Popen(
                [python_exe, str(main_py)],
                cwd=str(project_root),
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
            log.info("Masaustu UI baslatildi.")
            return {"success": True, "message": "Jarvis UI baslatildi."}
        except Exception as exc:
            log.error("UI baslatilamadi: %s", exc)
            raise HTTPException(500, f"UI baslatilamadi: {exc}")

    # ─── Autostart Endpoints ───────────────────────────────────────────

    @app.post("/api/autostart/install")
    async def autostart_install(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        from remote.autostart import install
        import asyncio
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, install)
        return {"success": success}

    @app.post("/api/autostart/uninstall")
    async def autostart_uninstall(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        from remote.autostart import uninstall
        import asyncio
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, uninstall)
        return {"success": success}

    @app.get("/api/autostart/status")
    async def autostart_status(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erisim")
        from remote.autostart import is_installed
        return {"installed": is_installed()}

    # ─── Emergency Lock Endpoints ──────────────────────────────────────

    @app.post("/api/security/lock")
    async def emergency_lock_endpoint(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        
        success, result = emergency_lock.lock_workstation()
        if success:
            _ntfy = ntfy_holder.get("client") if ntfy_holder else None
            if _ntfy:
                _ntfy.send_alert(f"🚨 ACİL KİLİT AKTİF!\nBilgisayarınız kilitlendi.\n\nGeçici Şifre: {result}")
            return {"success": True, "password": result}
        else:
            return {"success": False, "message": result}

    @app.post("/api/security/unlock")
    async def emergency_unlock_endpoint(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        
        success, message = emergency_lock.unlock_workstation()
        _ntfy = ntfy_holder.get("client") if ntfy_holder else None
        if success and _ntfy:
            _ntfy.send_status("🔓 ACİL KİLİT DEVREDEN ÇIKTI.\nŞifre başarıyla temizlendi.")
        return {"success": success, "message": message}

    @app.get("/api/security/status")
    async def emergency_status_endpoint(request: Request):
        if not require_session(request):
            raise HTTPException(401, "Yetkisiz erişim")
        
        return {
            "locked": emergency_lock.is_locked,
            "password": emergency_lock.temp_password
        }

    return app
