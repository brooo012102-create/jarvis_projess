import os
import sys
import subprocess
from pathlib import Path
import json
import platform

def install():
    print("="*60)
    print("JARVIS MARK XXXIX - OTOMATİK KURULUM")
    print("="*60)

    project_root = Path(__file__).resolve().parent

    # 1. Install Requirements
    print("\n[1/4] Python Kütüphaneleri Yükleniyor...")
    req_file = project_root / "requirements.txt"
    if req_file.exists():
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req_file)], check=True)
    else:
        print("Uyarı: requirements.txt bulunamadı!")

    # 2. Install Playwright Browsers
    print("\n[2/4] Playwright Tarayıcıları Yükleniyor...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
    except subprocess.CalledProcessError:
        print("Uyarı: Playwright kurulumu başarısız oldu veya atlandı.")

    # 3. Create Config Directory & Config Files
    print("\n[3/4] Konfigürasyon Dosyaları Hazırlanıyor...")
    config_dir = project_root / "config"
    config_dir.mkdir(exist_ok=True)

    # --- api_keys.json ---
    api_key_file = config_dir / "api_keys.json"
    existing_api = {}
    if api_key_file.exists():
        try:
            existing_api = json.loads(api_key_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    need_api_key = not existing_api.get("gemini_api_key")
    need_os = not existing_api.get("os_system")

    if need_api_key:
        print("\n  📋 Gemini API Anahtarı gerekli.")
        print("     Ücretsiz almak için: https://aistudio.google.com/apikey")
        api_key = input("  ➤ API Anahtarı: ").strip()
    else:
        api_key = existing_api["gemini_api_key"]

    if need_os:
        detected = platform.system()
        os_map = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}
        default_os = os_map.get(detected, "windows")
        os_input = input(f"  ➤ İşletim Sistemi (windows/mac/linux) [{default_os}]: ").strip().lower()
        os_system = os_input if os_input in ("windows", "mac", "linux") else default_os
    else:
        os_system = existing_api["os_system"]

    api_key_file.write_text(
        json.dumps({"gemini_api_key": api_key, "os_system": os_system}, indent=4),
        encoding="utf-8",
    )
    print("  ✓ config/api_keys.json kaydedildi.")

    # --- remote_config.json ---
    remote_cfg_file = config_dir / "remote_config.json"
    existing_remote = {}
    if remote_cfg_file.exists():
        try:
            existing_remote = json.loads(remote_cfg_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    need_ntfy = not existing_remote.get("ntfy_topic")

    if need_ntfy:
        print("\n  📱 ntfy kanal adı gerekli (telefona bildirim için).")
        print("     Telefonunuza 'ntfy' uygulamasını indirin ve bir kanal seçin.")
        ntfy_topic = input("  ➤ ntfy Kanal Adı: ").strip()
    else:
        ntfy_topic = existing_remote["ntfy_topic"]

    existing_remote["ntfy_topic"] = ntfy_topic
    # Varsayılanları tamamla
    defaults = {
        "ntfy_server": "https://ntfy.sh",
        "trusted_mac": "",
        "mac_scan_interval": 60,
        "server_port": 8765,
        "screen_fps": 15,
        "screen_quality": 50,
        "screen_scale": 0.5,
    }
    for k, v in defaults.items():
        existing_remote.setdefault(k, v)

    remote_cfg_file.write_text(
        json.dumps(existing_remote, indent=4),
        encoding="utf-8",
    )
    print("  ✓ config/remote_config.json kaydedildi.")

    # 4. Cloudflared check (Windows only)
    print("\n[4/4] Cloudflared Kontrolü...")
    if platform.system() == "Windows":
        try:
            result = subprocess.run(["cloudflared", "--version"], capture_output=True, text=True)
            if "cloudflared" in result.stdout:
                print("-> Cloudflared yüklü:", result.stdout.strip())
        except FileNotFoundError:
            print("-> Cloudflared bulunamadı. Uzaktan bağlantı için WinGet ile yükleniyor...")
            try:
                subprocess.run(["winget", "install", "--id", "Cloudflare.cloudflared", "--accept-source-agreements", "--accept-package-agreements"], check=True)
                print("-> Cloudflared başarıyla yüklendi!")
            except subprocess.CalledProcessError:
                print("-> Uyarı: Cloudflared yüklenemedi. Manuel olarak yüklemeniz gerekebilir.")
    else:
        print("-> Windows harici sistem: Lütfen Cloudflared'i paket yöneticinizle yükleyin.")

    print("\n" + "="*60)
    print("[OK] KURULUM TAMAMLANDI!")
    print("")
    print("Artık doğrudan başlatabilirsiniz:")
    print("  Masaüstü asistan : python main.py")
    print("  Uzaktan kontrol : python remote.py")
    print("="*60)

if __name__ == "__main__":
    install()
