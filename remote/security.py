"""
remote/security.py — MAC address monitoring for device security.

Periodically scans the local ARP table and checks if a trusted device
is present on the network. Sends alerts via ntfy if it disappears.
"""

import logging
import re
import subprocess
import sys
import threading
import time
import os
import string
import random

log = logging.getLogger("remote.security")

MAC_PATTERN = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")


class MacScanner:
    """
    Monitors local network for a trusted MAC address.
    Sends ntfy alerts when the device appears/disappears.
    """

    def __init__(self, trusted_mac: str, ntfy_client=None, interval: int = 60):
        self.trusted_mac = self._normalize(trusted_mac)
        self.ntfy = ntfy_client
        self.interval = max(15, interval)  # minimum 15 seconds

        self._stop_event = threading.Event()
        self._was_present = True  # assume present initially to avoid false alarm on start
        self._first_scan = True

    # ─── Public API ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start periodic MAC scanning in a background thread."""
        if not self.trusted_mac:
            log.warning("Güvenilir MAC adresi tanımlanmamış, tarama devre dışı.")
            return

        self._stop_event.clear()
        t = threading.Thread(target=self._monitor_loop, name="mac-scanner", daemon=True)
        t.start()
        log.info(
            "MAC tarayıcı başlatıldı — Hedef: %s, Aralık: %ds",
            self.trusted_mac, self.interval,
        )

    def stop(self) -> None:
        self._stop_event.set()
        log.info("MAC tarayıcı durduruldu.")

    def scan_now(self) -> set[str]:
        """Run a single ARP scan. Returns set of normalized MAC addresses."""
        return self._arp_scan()

    def is_trusted_present(self) -> bool:
        """Check if the trusted MAC is currently on the network."""
        macs = self._arp_scan()
        return self.trusted_mac in macs

    # ─── Internal ───────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            try:
                macs = self._arp_scan()
                present = self.trusted_mac in macs

                if self._first_scan:
                    self._was_present = present
                    self._first_scan = False
                    state = "agda bulundu" if present else "agda bulunamadi"
                    log.info("Ilk tarama: Güvenilir cihaz %s (%d cihaz tespit edildi)", state, len(macs))

                    if not present and self.ntfy:
                        self.ntfy.send_alert(
                            f"Guvenilir cihaz ({self.trusted_mac}) yerel agda bulunamadi!\n"
                        )
                elif present and not self._was_present:
                    # Device came back
                    log.info("Guvenilir cihaz tekrar agda: %s", self.trusted_mac)
                    if self.ntfy:
                        self.ntfy.send_status(
                            f"Guvenilir cihaz tekrar agda.\nMAC: {self.trusted_mac}"
                        )
                    self._was_present = True

                elif not present and self._was_present:
                    # Device disappeared
                    log.warning("Guvenilir cihaz agdan ayrildi: %s", self.trusted_mac)
                    if self.ntfy:
                        self.ntfy.send_alert(
                            f"Guvenilir cihaz ({self.trusted_mac}) agdan ayrildi!\n"
                            f"Agdaki cihaz sayisi: {len(macs)}"
                        )
                    self._was_present = False

            except Exception as exc:
                log.error("MAC tarama hatası: %s", exc)

            # Wait with stop-event awareness
            self._stop_event.wait(timeout=self.interval)

    def _arp_scan(self) -> set[str]:
        """Parse the system ARP table. Works on Windows/Linux/macOS."""
        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )

            if result.returncode != 0:
                log.warning("ARP komutu hata döndü (code=%d)", result.returncode)
                return set()

            macs = set()
            for match in MAC_PATTERN.finditer(result.stdout):
                mac = self._normalize(match.group(0))
                if mac and mac != "ff:ff:ff:ff:ff:ff" and mac != "00:00:00:00:00:00":
                    macs.add(mac)

            return macs

        except subprocess.TimeoutExpired:
            log.warning("ARP komutu zaman aşımına uğradı.")
            return set()
        except FileNotFoundError:
            log.error("'arp' komutu bulunamadı.")
            return set()

    @staticmethod
    def _normalize(mac: str) -> str:
        """Normalize MAC address to lowercase colon-separated format."""
        if not mac:
            return ""
        mac = mac.strip().lower().replace("-", ":")
        # Validate format
        if MAC_PATTERN.match(mac):
            return mac
        return ""


class EmergencyLock:
    """
    Handles emergency workstation locking and temporary password assignment.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.is_locked = False
        self.temp_password = None
        self._monitor_thread = None

    def _change_password(self, old_pass: str, new_pass: str) -> tuple[bool, str]:
        import ctypes
        try:
            netapi32 = ctypes.WinDLL('netapi32')
            username = os.environ.get("USERNAME")
            if not username:
                return False, "Kullanıcı adı bulunamadı."
            
            res = netapi32.NetUserChangePassword(
                None, 
                ctypes.c_wchar_p(username), 
                ctypes.c_wchar_p(old_pass), 
                ctypes.c_wchar_p(new_pass)
            )
            
            if res == 0:
                return True, "Başarılı"
            elif res == 86: # ERROR_INVALID_PASSWORD
                return False, "Sistemde zaten bir şifre var (Eski şifre hatası)."
            elif res == 5: # ERROR_ACCESS_DENIED
                return False, "Erişim reddedildi (Admin yetkisi gerekebilir)."
            elif res == 2245: # NERR_PasswordTooShort
                return False, "Şifre Windows ilkelerine göre çok kısa."
            else:
                return False, f"Windows API Hata Kodu: {res}"
        except Exception as e:
            return False, f"API Hatası: {e}"

    def lock_workstation(self) -> tuple[bool, str]:
        """
        Generates a password, sets it for the current user, and locks the PC.
        Returns (success, password_or_error_msg).
        """
        if sys.platform != "win32":
            return False, "Bu özellik sadece Windows'ta desteklenir."

        with self._lock:
            if self.is_locked:
                return True, self.temp_password

            # Generate short secure password (e.g. JRV-X9B)
            chars = string.ascii_uppercase + string.digits
            self.temp_password = "JRV-" + "".join(random.choices(chars, k=3))

            # Set the password using Windows API
            # Assuming the user currently has an empty password ("")
            success, msg = self._change_password("", self.temp_password)
            
            if not success:
                log.error("Acil kilit şifresi ayarlanamadı: %s", msg)
                # We still lock the workstation, but without password change.
                self._do_lock()
                return False, msg

            log.info("Acil kilit aktifleştirildi. Geçici şifre atandı: %s", self.temp_password)
            self.is_locked = True

            # Lock the screen
            self._do_lock()

            # Start monitoring unlock
            self._monitor_thread = threading.Thread(target=self._monitor_unlock_loop, daemon=True)
            self._monitor_thread.start()

            return True, self.temp_password

    def unlock_workstation(self) -> tuple[bool, str]:
        """
        Clears the current user's password.
        """
        if sys.platform != "win32":
            return False, "Desteklenmeyen platform"

        with self._lock:
            if not self.is_locked:
                return True, "Zaten kilitli değil."

            # Clear password (change from temp_password to "")
            success, msg = self._change_password(self.temp_password, "")
            
            if not success:
                # Just in case they manually removed the password, try changing "" to ""
                success2, msg2 = self._change_password("", "")
                if not success2:
                    log.error("Şifre kaldırılamadı: %s", msg)
                    return False, f"Şifre silinemedi: {msg}"

            log.info("Acil kilit devreden çıkarıldı. Şifre temizlendi.")
            self.is_locked = False
            self.temp_password = None
            return True, "Şifre temizlendi."

    def _do_lock(self):
        try:
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        except Exception as e:
            log.error("Bilgisayar kilitlenemedi: %s", e)

    def _monitor_unlock_loop(self):
        """Monitors when the session becomes unlocked."""
        import ctypes
        
        # Ekranın tam kilitlenmesi için 10 saniye bekle
        time.sleep(10)
        
        unlocked_count = 0
        while self.is_locked:
            time.sleep(2)
            try:
                # OpenInputDesktop returns a handle > 0 if desktop is active/unlocked.
                desk = ctypes.windll.User32.OpenInputDesktop(0, False, 0x0100)
                if desk:
                    ctypes.windll.User32.CloseDesktop(desk)
                    unlocked_count += 1
                else:
                    unlocked_count = 0
                
                # Sadece ard arda 3 kez (6 saniye boyunca) açık görürse kilidi kaldır
                if unlocked_count >= 3:
                    log.info("Fiziksel ekran kilidinin kalıcı olarak açıldığı tespit edildi.")
                    self.unlock_workstation()
                    break
            except Exception:
                pass
