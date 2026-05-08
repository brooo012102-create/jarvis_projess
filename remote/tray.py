"""
remote/tray.py — System tray icon for Jarvis Remote.
"""
import logging
import threading
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

log = logging.getLogger("remote.tray")

class TrayIcon:
    def __init__(self, on_resend_click, on_exit_click):
        self.on_resend_click = on_resend_click
        self.on_exit_click = on_exit_click
        self.icon = None

    def create_image(self):
        # Generate a premium looking 'J' icon
        width = 64
        height = 64
        bg_color = "#111111"
        accent_color = "#ff3c3c"

        image = Image.new('RGB', (width, height), bg_color)
        dc = ImageDraw.Draw(image)
        
        # Draw a red circle outline
        dc.ellipse(
            [(4, 4), (width - 4, height - 4)],
            outline=accent_color,
            width=4
        )
        
        # Draw 'J' shape
        # Top bar
        dc.rectangle([32, 16, 48, 22], fill=accent_color)
        # Stem
        dc.rectangle([40, 22, 46, 44], fill=accent_color)
        # Bottom hook
        dc.rectangle([24, 38, 46, 44], fill=accent_color)
        dc.rectangle([24, 30, 30, 44], fill=accent_color)
        
        return image

    def start(self):
        image = self.create_image()
        menu = pystray.Menu(
            item('Durum: Çalışıyor', lambda: None, enabled=False),
            item('Yeniden Bildirim Gönder', self._on_resend),
            item('Çıkış', self._on_exit)
        )
        self.icon = pystray.Icon("Jarvis Remote", image, "Jarvis Remote", menu)
        
        # Run pystray using its native detached method
        self.icon.run_detached()
        log.info("Sistem tepsisi (tray) simgesi başlatıldı.")

    def _on_resend(self, icon, item):
        log.info("Kullanıcı tray üzerinden bildirim gönderimi talep etti.")
        self.on_resend_click()

    def _on_exit(self, icon, item):
        log.info("Kullanıcı tray üzerinden çıkış talep etti.")
        self.icon.stop()
        self.on_exit_click()

    def stop(self):
        if self.icon:
            self.icon.stop()
