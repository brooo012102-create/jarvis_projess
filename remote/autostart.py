"""
remote/autostart.py — Windows otomatik başlatma yöneticisi.

PC açıldığında remote.py'yi arka planda (konsolsuz) başlatır.
Windows Task Scheduler kullanır.
"""

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("remote.autostart")

TASK_NAME = "JarvisRemoteControl"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_python_exe() -> str:
    """pythonw.exe kullan (konsolsuz), yoksa python.exe."""
    # Prioritize local virtual environment
    project_root = Path(__file__).resolve().parent.parent
    venv_pythonw = project_root / "venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return str(venv_pythonw)
        
    # Fallback to absolute known path
    known_path = Path(r"C:\Users\Berat\AppData\Local\Programs\Python\Python311\pythonw.exe")
    if known_path.exists():
        return str(known_path)
    
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def _get_vbs_path() -> Path:
    """Gizli başlatma VBS scripti yolu."""
    return PROJECT_ROOT / "start_remote_hidden.vbs"


def _create_vbs_launcher():
    """
    VBS wrapper oluştur — Python'u tamamen gizli başlatır.
    pythonw.exe bile bazen kısa bir pencere gösterebilir,
    VBS bunu tamamen engeller.
    """
    vbs_path = _get_vbs_path()
    python_exe = _get_python_exe()
    remote_py = PROJECT_ROOT / "remote.py"

    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{PROJECT_ROOT}"
WshShell.Run """{python_exe}"" ""{remote_py}""", 0, False
'''
    vbs_path.write_text(vbs_content, encoding="utf-8")
    log.info("VBS launcher oluşturuldu: %s", vbs_path)
    return vbs_path


import winreg

def install() -> bool:
    """
    Windows Kayit Defteri (Registry) kullanarak otomatik baslatma ekle.
    Yonetici izni gerektirmez.
    """
    try:
        vbs_path = _create_vbs_launcher()

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, f'wscript.exe "{vbs_path}"')

        log.info("Otomatik baslatma kuruldu: %s", TASK_NAME)
        return True

    except Exception as exc:
        log.error("Otomatik baslatma kurulamadi: %s", exc)
        return False


def uninstall() -> bool:
    """Otomatik baslatma gorevini kaldir."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, TASK_NAME)

        # VBS dosyasini sil
        vbs_path = _get_vbs_path()
        if vbs_path.exists():
            vbs_path.unlink()

        log.info("Otomatik baslatma kaldirildi.")
        return True

    except FileNotFoundError:
        return True
    except Exception as exc:
        log.error("Kaldirma hatasi: %s", exc)
        return False


def is_installed() -> bool:
    """Gorevin kurulu olup olmadigini kontrol et."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, TASK_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Kullanim:")
        print("  python -m remote.autostart install    — Otomatik baslatma kur")
        print("  python -m remote.autostart uninstall  — Otomatik baslatma kaldir")
        print("  python -m remote.autostart status     — Durum kontrol")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "status":
        if is_installed():
            print(f"[OK] '{TASK_NAME}' gorevi kurulu ve aktif.")
        else:
            print(f"[BILGI] '{TASK_NAME}' gorevi kurulu degil.")
    else:
        print(f"Bilinmeyen komut: {cmd}")
