import time
import subprocess
import sys # Tambahkan import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class RestartHandler(FileSystemEventHandler):
    def __init__(self, script):
        self.script = script
        self.process = None
        self.start_script()

    def start_script(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
        print("🔄 Menjalankan ulang bot...")
        
        # BARIS PERUBAHAN UTAMA:
        # Menggunakan sys.executable agar lebih aman dan portabel
        self.process = subprocess.Popen([sys.executable, self.script])

    def on_modified(self, event):
        if event.src_path.endswith(self.script):
            print(f"💾 Terdeteksi perubahan di {self.script}")
            self.start_script()

if __name__ == "__main__":
    # Pastikan file vee.py berada di direktori yang sama
    script_name = "vee.py" 
    
    # Cek apakah script_name adalah file yang valid
    if not script_name in sys.argv[0]:
        event_handler = RestartHandler(script_name)
    else:
        # Jika auto_restart.py dijalankan sebagai bagian dari vee.py (tidak mungkin dalam kasus ini)
        # atau jika ada kesalahan penamaan, ini mencegah loop tak terhingga.
        print("⚠️ Kesalahan konfigurasi script_name. Memerlukan 'vee.py'.")
        sys.exit(1)

    observer = Observer()
    observer.schedule(event_handler, ".", recursive=False)
    observer.start()
    print(f"👀 Memantau perubahan di {script_name}...\nTekan CTRL+C untuk berhenti.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        if event_handler.process:
            event_handler.process.terminate()
    observer.join()
