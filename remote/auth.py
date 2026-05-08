"""
remote/auth.py — Two-phase authentication system (Token + OTP).

Flow:
  1. User opens URL → enters SESSION TOKEN (sent via ntfy on startup)
  2. Token valid → server generates 6-digit OTP → sent via ntfy
  3. OTP valid → session cookie set → dashboard access granted
"""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("remote.auth")

# ─── Constants ──────────────────────────────────────────────────────────────

OTP_LENGTH = 6
OTP_TTL = 120          # 2 minutes
SESSION_TTL = 3600     # 1 hour
MAX_FAILED_ATTEMPTS = 5
RATE_LIMIT_WINDOW = 600  # 10 minutes


# ─── Data Classes ───────────────────────────────────────────────────────────

@dataclass
class OTPRecord:
    code: str
    created_at: float
    used: bool = False


@dataclass
class Session:
    session_id: str
    ip: str
    created_at: float
    last_active: float


@dataclass
class RateLimitEntry:
    attempts: int = 0
    first_attempt: float = 0.0


# ─── Auth Manager ───────────────────────────────────────────────────────────

class AuthManager:
    """
    Thread-safe authentication manager.
    Generates session tokens, OTP codes, and manages active sessions.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._session_token: str = ""
        self._otp: OTPRecord | None = None
        self._sessions: dict[str, Session] = {}       # session_id → Session
        self._rate_limits: dict[str, RateLimitEntry] = {}  # ip → RateLimitEntry
        self._token_verified_ips: set[str] = set()  # IPs that passed token phase

        # Generate initial session token
        self.regenerate_token()

    # ─── Token Management ───────────────────────────────────────────────

    def regenerate_token(self) -> str:
        """Generate a new session token. Called on every tunnel restart."""
        with self._lock:
            self._session_token = secrets.token_urlsafe(32)
            self._token_verified_ips.clear()
            self._otp = None
            log.info("Yeni oturum anahtarı oluşturuldu.")
            return self._session_token

    @property
    def session_token(self) -> str:
        with self._lock:
            return self._session_token

    def verify_token(self, token: str, ip: str) -> bool:
        """
        Verify the session token (constant-time comparison).
        Returns True if token is correct and IP is not rate-limited.
        """
        with self._lock:
            if self._is_rate_limited(ip):
                log.warning("IP hız sınırına ulaştı: %s", ip)
                return False

            result = hmac.compare_digest(token, self._session_token)

            if result:
                self._token_verified_ips.add(ip)
                self._reset_rate_limit(ip)
                log.info("Oturum anahtarı doğrulandı: %s", ip)
            else:
                self._record_failed_attempt(ip)
                log.warning("Hatalı oturum anahtarı: %s", ip)

            return result

    def is_token_verified(self, ip: str) -> bool:
        """Check if an IP has passed the token verification phase."""
        with self._lock:
            return ip in self._token_verified_ips

    # ─── OTP Management ────────────────────────────────────────────────

    def generate_otp(self) -> str:
        """Generate a new 6-digit OTP code. Previous OTP is invalidated."""
        with self._lock:
            code = str(secrets.randbelow(10 ** OTP_LENGTH)).zfill(OTP_LENGTH)
            self._otp = OTPRecord(code=code, created_at=time.time())
            log.info("Yeni OTP oluşturuldu.")
            return code

    def verify_otp(self, code: str, ip: str) -> bool:
        """
        Verify OTP code. OTP is single-use and expires after OTP_TTL seconds.
        """
        with self._lock:
            if self._is_rate_limited(ip):
                log.warning("IP hız sınırına ulaştı (OTP): %s", ip)
                return False

            if self._otp is None:
                self._record_failed_attempt(ip)
                return False

            if self._otp.used:
                self._record_failed_attempt(ip)
                log.warning("OTP zaten kullanıldı.")
                return False

            elapsed = time.time() - self._otp.created_at
            if elapsed > OTP_TTL:
                self._otp = None
                self._record_failed_attempt(ip)
                log.warning("OTP süresi doldu (%ds).", int(elapsed))
                return False

            result = hmac.compare_digest(code.strip(), self._otp.code)

            if result:
                self._otp.used = True
                self._reset_rate_limit(ip)
                log.info("OTP doğrulandı: %s", ip)
            else:
                self._record_failed_attempt(ip)
                log.warning("Hatalı OTP: %s", ip)

            return result

    # ─── Session Management ─────────────────────────────────────────────

    def create_session(self, ip: str) -> str:
        """Create a new authenticated session. Returns session ID."""
        with self._lock:
            session_id = secrets.token_urlsafe(48)
            self._sessions[session_id] = Session(
                session_id=session_id,
                ip=ip,
                created_at=time.time(),
                last_active=time.time(),
            )
            log.info("Oturum oluşturuldu: %s [%s]", session_id[:12] + "…", ip)
            return session_id

    def validate_session(self, session_id: str) -> bool:
        """Check if a session is valid (exists and not expired)."""
        if not session_id:
            return False

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False

            elapsed = time.time() - session.created_at
            if elapsed > SESSION_TTL:
                del self._sessions[session_id]
                log.info("Oturum süresi doldu: %s", session_id[:12] + "…")
                return False

            session.last_active = time.time()
            return True

    def invalidate_all_sessions(self) -> None:
        """Clear all active sessions (called on tunnel restart)."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._token_verified_ips.clear()
            self._otp = None
            if count > 0:
                log.info("%d oturum sonlandırıldı.", count)

    def cleanup_expired(self) -> None:
        """Remove expired sessions and rate-limit entries."""
        with self._lock:
            now = time.time()

            # Expired sessions
            expired = [
                sid for sid, s in self._sessions.items()
                if now - s.created_at > SESSION_TTL
            ]
            for sid in expired:
                del self._sessions[sid]

            # Expired rate limits
            expired_rl = [
                ip for ip, rl in self._rate_limits.items()
                if now - rl.first_attempt > RATE_LIMIT_WINDOW
            ]
            for ip in expired_rl:
                del self._rate_limits[ip]

    # ─── Rate Limiting (must be called under lock) ──────────────────────

    def _is_rate_limited(self, ip: str) -> bool:
        entry = self._rate_limits.get(ip)
        if entry is None:
            return False

        # Reset window if expired
        if time.time() - entry.first_attempt > RATE_LIMIT_WINDOW:
            del self._rate_limits[ip]
            return False

        return entry.attempts >= MAX_FAILED_ATTEMPTS

    def _record_failed_attempt(self, ip: str) -> None:
        entry = self._rate_limits.get(ip)
        now = time.time()

        if entry is None or (now - entry.first_attempt > RATE_LIMIT_WINDOW):
            self._rate_limits[ip] = RateLimitEntry(attempts=1, first_attempt=now)
        else:
            entry.attempts += 1

    def _reset_rate_limit(self, ip: str) -> None:
        self._rate_limits.pop(ip, None)
