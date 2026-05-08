"""
remote/audio.py — System volume control via pycaw (Windows).
Thread-safe: COM is initialized per-call since pycaw uses COM objects.
"""
import logging, subprocess, sys
log = logging.getLogger("remote.audio")

def _get_volume_interface():
    """Get IAudioEndpointVolume interface - compatible with all pycaw versions."""
    import comtypes
    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from ctypes import cast, POINTER
    
    speakers = AudioUtilities.GetSpeakers()
    
    # Try modern pycaw API first (Activate method)
    try:
        interface = speakers.Activate(IAudioEndpointVolume._iid_, 23, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except AttributeError:
        pass
    
    # Fallback: older pycaw where GetSpeakers returns endpoint volume directly
    try:
        if hasattr(speakers, 'EndpointVolume'):
            return speakers.EndpointVolume
    except Exception:
        pass
    
    # Last resort: use the speakers object's QueryInterface
    try:
        from comtypes import GUID
        iid = GUID('{5CDF2C82-841E-4546-9722-0CF74078229A}')
        interface = speakers.QueryInterface(iid)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        pass
    
    raise RuntimeError("Could not get audio endpoint volume interface")

class AudioController:
    """Thread-safe system volume controller using pycaw."""

    def get_volume(self) -> int:
        try:
            import comtypes; comtypes.CoInitialize()
            try:
                vol = _get_volume_interface()
                return int(round(vol.GetMasterVolumeLevelScalar() * 100))
            finally:
                comtypes.CoUninitialize()
        except Exception as exc:
            log.error("Ses seviyesi okunamadi: %s", exc)
            return self._fallback_get_volume()

    def set_volume(self, level: int) -> bool:
        level = max(0, min(100, level))
        try:
            import comtypes; comtypes.CoInitialize()
            try:
                vol = _get_volume_interface()
                vol.SetMasterVolumeLevelScalar(level / 100.0, None)
                return True
            finally:
                comtypes.CoUninitialize()
        except Exception as exc:
            log.error("Ses ayarlanamadi: %s", exc)
            return self._fallback_set_volume(level)

    def get_mute(self) -> bool:
        try:
            import comtypes; comtypes.CoInitialize()
            try:
                vol = _get_volume_interface()
                return bool(vol.GetMute())
            finally:
                comtypes.CoUninitialize()
        except:
            return False

    def set_mute(self, mute: bool) -> bool:
        try:
            import comtypes; comtypes.CoInitialize()
            try:
                vol = _get_volume_interface()
                vol.SetMute(int(mute), None)
                return True
            finally:
                comtypes.CoUninitialize()
        except:
            return False

    def toggle_mute(self) -> bool:
        current = self.get_mute()
        self.set_mute(not current)
        return not current

    @staticmethod
    def _fallback_get_volume() -> int:
        """Fallback volume read via PowerShell."""
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "(Get-AudioDevice -PlaybackVolume).Replace('%','')"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
            return int(result.stdout.strip())
        except:
            return 50

    @staticmethod
    def _fallback_set_volume(level: int) -> bool:
        """Fallback via nircmd if pycaw fails."""
        try:
            val = int(level / 100 * 65535)
            subprocess.run(["nircmd","setsysvolume",str(val)],capture_output=True,timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
            return True
        except:
            log.error("Yedek ses kontrol yontemi de basarisiz.")
            return False
