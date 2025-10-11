import os
import re
import random
import requests
import yt_dlp
import speech_recognition as sr
import sys
import logging
import json
import threading
import asyncio
from datetime import datetime
from colorama import init, Fore, Style
from dotenv import load_dotenv
from pydub import AudioSegment
from gtts import gTTS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
    Application
)
from watchdog.observers import Observer 
from watchdog.events import FileSystemEventHandler 

# ======== INISIALISASI DAN KONFIGURASI ========
init(autoreset=True)
sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Konfigurasi Logging Kustom ---
class ColorFormatter(logging.Formatter):
    COLORS = {
        'INFO': Fore.CYAN + Style.BRIGHT,
        'SUCCESS': Fore.GREEN + Style.BRIGHT,
        'WARNING': Fore.YELLOW + Style.BRIGHT,
        'ERROR': Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        reset = Style.RESET_ALL
        asctime = datetime.now().strftime("%H:%M:%S")
        return f"{Fore.WHITE}{asctime} | {color}{record.levelname:<7}{reset} | {record.msg}"

# Atur handler logging
logging.basicConfig(level=logging.INFO)
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter())
logging.root.addHandler(handler)

# Tambahan: Sembunyikan Log Spam HTTP Request dari httpx/httpcore
logging.getLogger("httpx").setLevel(logging.WARNING) 
logging.getLogger("httpcore").setLevel(logging.WARNING) 

# Shortcut Logging
def log_info(msg): logging.info(f"✨ {msg}")
def log_warn(msg): logging.warning(f"⚠️ {msg}")
def log_error(msg): logging.error(f"❌ {msg}")
def log_success(msg):
    record = logging.LogRecord("root", logging.INFO, "", 0, f"✅ {msg}", None, None)
    record.levelname = "SUCCESS"
    print(ColorFormatter().format(record))

# --- State Persistence ---
CHAT_IDS_FILE = "chat_ids.json"
LOADED_CHAT_IDS = set()
active_games = {} # Format: {chat_id: {'target': int, 'attempts_left': int, 'starter_id': int, 'message_id': int, 'delete_timer': threading.Timer or None}}
BOT_START_TIME = datetime.now()
# >>> YOUTUBE GLOBAL DICTIONARY for Lagu feature
# Menyimpan URL dan ID pesan hasil untuk referensi cepat
# Format: {f'{chat_id}_{result_message_id}': {'requestor_id': int, 'request_msg_id': int, 'url': str, 'messages_to_delete': [int]}}
youtube_requests = {} 

def load_chat_ids():
    global LOADED_CHAT_IDS
    if os.path.exists(CHAT_IDS_FILE):
        try:
            with open(CHAT_IDS_FILE, "r") as f:
                LOADED_CHAT_IDS = set(json.load(f))
            log_info(f"Loaded {len(LOADED_CHAT_IDS)} chat IDs.")
        except Exception as e:
            log_error(f"Gagal memuat chat IDs: {e}")
            LOADED_CHAT_IDS = set()
    else:
        log_warn("File Chat IDs tidak ditemukan, memulai dengan daftar kosong.")

def save_chat_ids():
    with open(CHAT_IDS_FILE, "w") as f:
        json.dump(list(LOADED_CHAT_IDS), f)
    log_info(f"Tersimpan {len(LOADED_CHAT_IDS)} chat IDs.")

# --- AUTO RESTART CONFIGURATION & CHECK (Menggunakan Watchdog) ---
MAIN_SCRIPT_FILE = "vee.py"
WATCHDOG_OBSERVER = None
APPLICATION_INSTANCE = None 

# Perbaikan: Mengganti logika restart_bot dengan penanganan penghentian bot yang lebih baik
def restart_bot(application: Application):
    """Fungsi untuk melakukan restart bot. Dipanggil dari thread Watchdog."""
    log_warn(f"Perubahan pada '{MAIN_SCRIPT_FILE}' terdeteksi! Melakukan auto-restart...")
    
    # Hentikan Watchdog Observer secara tegas
    global WATCHDOG_OBSERVER
    if WATCHDOG_OBSERVER and WATCHDOG_OBSERVER.is_alive():
        WATCHDOG_OBSERVER.stop()
        WATCHDOG_OBSERVER.join(timeout=3)
        log_info("Watchdog Observer dihentikan.")

    # Menghentikan Application (polling dan job_queue) secara thread-safe
    app_loop = application.loop
    if app_loop.is_running():
        # Menjadwalkan pemanggilan application.stop() di event loop utama
        async def shutdown():
            await application.stop()
            
        future = asyncio.run_coroutine_threadsafe(shutdown(), app_loop)
        try:
            future.result(timeout=5)
            log_info("Telegram Application dihentikan.")
        except Exception as e:
            log_error(f"Gagal menghentikan Application secara bersih: {e}. Melanjutkan restart.")
    
    # Melakukan restart
    # Menggunakan os.execl untuk mengganti proses saat ini dengan proses baru
    # Ini jauh lebih andal untuk restart script Python
    try:
        log_info(f"Memulai ulang bot: {sys.executable} {sys.argv[0]}")
        os.execl(sys.executable, sys.executable, *sys.argv)
    except Exception as e:
        log_error(f"Gagal melakukan os.execl untuk restart: {e}")

class FileChangeHandler(FileSystemEventHandler):
    """Handler event untuk memantau perubahan file."""
    def on_modified(self, event):
        # Hanya memproses event jika file yang diubah adalah script utama
        # Menggunakan os.path.abspath untuk perbandingan yang lebih pasti
        if not event.is_directory and os.path.abspath(event.src_path) == os.path.abspath(MAIN_SCRIPT_FILE):
            # Delay kecil untuk memastikan file selesai ditulis sebelum restart
            # Restart dipanggil dari thread lain, aman dari masalah blocking.
            threading.Timer(1.5, lambda: restart_bot(APPLICATION_INSTANCE)).start()
            
def start_watchdog(application: Application):
    """Memulai Watchdog Observer untuk memonitor file script."""
    global WATCHDOG_OBSERVER, APPLICATION_INSTANCE
    APPLICATION_INSTANCE = application
    
    # Dapatkan path direktori
    path = "."
    event_handler = FileChangeHandler()
    WATCHDOG_OBSERVER = Observer()
    
    # Cek apakah file ada sebelum memulai observer
    if not os.path.exists(MAIN_SCRIPT_FILE):
        log_error(f"File script utama '{MAIN_SCRIPT_FILE}' tidak ditemukan. Fitur auto-restart dinonaktifkan.")
        return

    WATCHDOG_OBSERVER.schedule(event_handler, path, recursive=False)
    WATCHDOG_OBSERVER.start()
    log_success(f"Watchdog Auto-Restart diaktifkan (Memantau '{MAIN_SCRIPT_FILE}').")

# --- END AUTO RESTART CONFIGURATION & CHECK ---

# ======== GEMINI AI FUNCTIONS ========
def chat_gemini(prompt: str) -> str:
    """Mengirim prompt ke model Gemini dan mengembalikan respons teks."""
    if not GEMINI_API_KEY:
        return "⚠️ Kunci API Gemini belum diatur di file .env."
        
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    data = {"contents": [{"parts": [{"text": prompt}]}]}
        
    try:
        # Tambahkan timeout 10 detik untuk request Gemini
        res = requests.post(url, json=data, timeout=10) 
        res.raise_for_status() # Raise exception for bad status codes
            
        return res.json()["candidates"][0]["content"]["parts"][0]["text"]
            
    except requests.exceptions.RequestException as e:
        log_error(f"Error request ke Gemini: {e}")
        return "⚠️ Ada masalah saat menghubungi server AI. Coba lagi nanti."
    except Exception:
        log_error("Gagal parse respons Gemini atau respons tidak valid.")
        return "⚠️ Aku agak bingung jawabnya nih."

def get_random_fact() -> str:
    """Mengambil fakta dunia acak dari Gemini."""
    prompt = "Berikan satu fakta dunia yang menarik dan unik dalam Bahasa Indonesia, dengan format yang singkat dan santai. Jangan gunakan judul atau emoji di awal kalimat, langsung saja isi faktanya."
    fact = chat_gemini(prompt)
    return fact

# ======== SCHEDULED JOB ========
async def send_scheduled_fact(context: ContextTypes.DEFAULT_TYPE):
    """Mengirim fakta dunia terjadwal ke semua chat ID yang terdaftar."""
    log_info("Memulai pengiriman fakta dunia terjadwal...")
    load_chat_ids()
        
    if not LOADED_CHAT_IDS:
        log_warn("Tidak ada chat ID yang terdaftar untuk pengiriman fakta.")
        return
        
    try:
        # Menjalankan fungsi blocking (get_random_fact) di executor
        fact = await asyncio.get_event_loop().run_in_executor(None, get_random_fact)
        message = f"✨ **Fakta Dunia Vee**\n\n{fact}"
            
        for chat_id in list(LOADED_CHAT_IDS):
            try:
                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=message, 
                    parse_mode="Markdown", 
                    disable_notification=True
                )
                log_info(f"Fakta berhasil dikirim ke chat ID: {chat_id}")
            except Exception as e:
                log_error(f"Gagal mengirim fakta ke chat ID {chat_id}: {e}")
                # Hapus chat ID jika bot dikeluarkan/diblokir
                error_message = str(e)
                if "bot was blocked" in error_message or "bot was kicked" in error_message or "chat not found" in error_message:
                    LOADED_CHAT_IDS.discard(chat_id)
                    log_warn(f"Chat ID {chat_id} dihapus dari daftar.")
                    save_chat_ids()

    except Exception as e:
        log_error(f"Gagal mengambil atau memproses fakta terjadwal: '{e}'")

# ======== VOICE & YOUTUBE FUNCTIONS ========
def text_to_speech(text: str, filename="reply.mp3") -> str:
    """Mengkonversi teks menjadi file audio MP3 menggunakan gTTS."""
    tts = gTTS(text=text, lang="id")
    tts.save(filename)
    return filename

# --- FUNGSI get_youtube_info DIUBAH UNTUK MENGAMBIL 3 HASIL ---
def get_youtube_info(query: str, max_results=3):
    """Mencari informasi N video YouTube teratas (judul, URL, durasi) tanpa mengunduh."""
    log_info(f"Mencari {max_results} lagu teratas di YouTube: {query}")
    # Mengubah format pencarian menjadi ytsearchN:query, di mana N adalah max_results
    ydl_opts = {"quiet": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        if "entries" in result and len(result["entries"]) > 0:
            # Mengembalikan list of entries
            return result["entries"][:max_results]
    return []

def format_duration(seconds: int) -> str:
    """Mengubah total detik menjadi format yang mudah dibaca (hari, jam, menit, detik)."""
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days} hari")
    if hours > 0:
        parts.append(f"{hours} jam")
    if minutes > 0:
        parts.append(f"{minutes} menit")
    if secs > 0 or not parts:
        parts.append(f"{secs} detik")
    return ", ".join(parts)

# >>> HELPER FUNCTION TO DELETE MULTIPLE MESSAGES
async def delete_messages_async(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list):
    """Menghapus list of message IDs secara asinkron."""
    for msg_id in message_ids:
        try:
            # Menggunakan delete_message
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            # Mengabaikan jika pesan sudah terhapus atau error lainnya
            pass 

# ======== GAME FUNCTIONS ========
def handle_game_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 0):
    """
    Menghapus pesan game tebak angka. Fungsi ini thread-safe.
    Dijalankan di event loop utama jika delay=0, atau di thread Timer jika delay>0.
    """
    # Fungsi Coroutine untuk penghapusan
    async def delete_async(bot, chat_id, message_id):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            log_info(f"Pesan game tebak angka di chat {chat_id} (ID: {message_id}) dihapus.")
        except Exception:
            pass 
            
    if delay > 0:
        # Jika ada delay, jalankan di thread Timer, lalu kirim kembali ke event loop
        app_bot = context.application.bot
        app_loop = context.application.loop
            
        def delete_task_threadsafe():
            # Mengirim coroutine ke event loop dari thread lain
            if app_loop.is_running():
                asyncio.run_coroutine_threadsafe(delete_async(app_bot, chat_id, message_id), app_loop)
            
        timer = threading.Timer(delay, delete_task_threadsafe)
        timer.start()
        return timer
    else:
        # Jika tidak ada delay, kita berada di handler async, langsung await
        try:
            # Karena ini dipanggil dari handler async (/tebakangka), kita bisa langsung await
            context.application.create_task(delete_async(context.bot, chat_id, message_id))
        except Exception as e:
            log_warn(f"Gagal menghapus pesan game tebak angka segera: {e}")
        return None

# ======== COMMAND HANDLERS ========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /start."""
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
        
    # Simpan ID grup
    if chat_type in ["group", "supergroup"]:
        global LOADED_CHAT_IDS
        if chat_id not in LOADED_CHAT_IDS:
            LOADED_CHAT_IDS.add(chat_id)
            save_chat_ids()
            log_success(f"New group chat ID added: {chat_id}")
            
    log_info("Perintah /start dipanggil")
    await update.message.reply_text(
        "Halo! 😎 Aku Vee.\n"
        "Kamu bisa tanya apa saja maupun request lagu 🎵\n"
        "Coba aja sebut namaku biar aku jawab 😉\n\n"
        "Menu:\n"
        "/lagu [judul lagu]\n"
        "/tebakangka\n"
        "/cek\n"
        "/uptime"
    )

async def cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /cek."""
    log_info("Perintah /cek dipanggil")
    await update.message.reply_text("Cek apa?! 😠\nSaya tidak diperbolehkan tidur🤯")

async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /uptime."""
    log_info("Perintah /uptime dipanggil")
    now = datetime.now()
    diff = int((now - BOT_START_TIME).total_seconds())
    formatted = format_duration(diff)
    await update.message.reply_text(f"⏱️ Bot sudah online selama {formatted}.")

# --- FUNGSI lagu DIUBAH UNTUK MENGGUNAKAN CALLBACK DATA RINGKAS---
async def lagu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk perintah /lagu."""
    if not context.args:
        await update.message.reply_text("Formatnya salah oon 😅\nCoba: /lagu Sia - Unstoppable")
        return

    query_text = " ".join(context.args)
    log_info(f"Perintah /lagu: {query_text} (Menampilkan 3 hasil)")
        
    requestor_id = update.message.from_user.id
    request_msg_id = update.message.message_id # ID pesan command /lagu
    
    # Inisialisasi list untuk menyimpan ID pesan hasil yang *akan* ditampilkan
    result_message_ids = []

    # Kirim status awal
    searching_msg = await update.message.reply_text(f"🔍 Sedang Mencari 3 Lagu Teratas: {query_text}")

    # Panggil fungsi blocking di executor (mengambil 3 hasil)
    info_list = await asyncio.get_event_loop().run_in_executor(None, get_youtube_info, query_text, 3)
        
    # Hapus pesan 'Searching'
    try:
        await searching_msg.delete()
    except Exception:
        pass # Abaikan jika gagal menghapus
        
    if not info_list:
        await update.message.reply_text("Waduh, lagunya gak ketemu😔")
        return

    # Kirim pesan hitungan hasil (Message #1 to delete)
    results_count_msg = await update.message.reply_text(f"Ditemukan **{len(info_list)}** hasil teratas untuk: **{query_text}**", parse_mode="Markdown")
    result_message_ids.append(results_count_msg.message_id)

    # Inisialisasi dictionary untuk menyimpan URL dan ID peminta
    # Ini akan digunakan di `button` untuk mengambil URL asli
    global youtube_requests
    
    for index, info in enumerate(info_list):
        title = info.get("title", f"Tidak diketahui ({index+1})")
        # Menggunakan ID video untuk URL yang lebih stabil
        video_id = info.get('id')
        url = f"https://www.youtube.com/watch?v={video_id}"
        duration = info.get("duration", 0)
        thumbnail = info.get("thumbnail")
        mins, secs = divmod(duration, 60)
        duration_str = f"{int(mins)}:{int(secs):02d}" if duration else "?"

        text = (
            f"**🎵 Hasil #{index + 1}**\n"
            f"<b>{title}</b>\n"
            f"⏱️ Durasi: {duration_str}\n"
            f"📺 <a href='{url}'>Tonton di YouTube</a>"
        )
        
        # Kirim metadata (Message #2, #3, #4 to delete)
        try:
            if thumbnail:
                result_msg = await update.message.reply_photo(
                    photo=thumbnail,
                    caption=text,
                    parse_mode="HTML"
                )
            else:
                result_msg = await update.message.reply_text(text, parse_mode="HTML")
            
            # --- START PERBAIKAN CALLBACK DATA ---
            result_msg_id = result_msg.message_id
            
            # 1. Simpan data lengkap ke dictionary global menggunakan ID pesan hasil
            request_key_result = f"{update.message.chat_id}_{result_msg_id}"
            youtube_requests[request_key_result] = {
                'requestor_id': requestor_id,
                'request_msg_id': request_msg_id,
                'url': url,
            }

            # 2. Buat callback data yang ringkas (Hanya [download|IDpesanHasil|IDpesanPerintah])
            callback_data_with_id = f"dl|{result_msg_id}|{request_msg_id}"
            
            keyboard = [[InlineKeyboardButton("⬇️ Download Song", callback_data=callback_data_with_id)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # 3. Edit pesan hasil untuk menambahkan tombol dengan callback data ringkas
            await result_msg.edit_reply_markup(reply_markup=reply_markup)
            
            # Simpan ID pesan hasil
            result_message_ids.append(result_msg_id) 

        except Exception as e:
            # Catch error Button_data_invalid here if edit_reply_markup was used directly
            # or any other sending error.
            log_error(f"Gagal mengirim/memproses hasil lagu #{index + 1}: {e}")
            await update.message.reply_text(f"⚠️ Gagal menampilkan hasil #{index + 1}.")


    # Simpan daftar ID pesan hasil ke global storage, menggunakan ID pesan perintah sebagai kunci
    # Ini harus dilakukan setelah semua pesan hasil (yang berisi URL asli) sudah dikirim/diedit
    request_key_command = f"{update.message.chat_id}_{request_msg_id}"
    
    # Gabungkan semua pesan yang akan dihapus: Pesan hitungan + semua pesan hasil
    all_messages_to_delete = result_message_ids
    
    # Simpan list pesan yang akan dihapus di kunci command
    youtube_requests[request_key_command] = all_messages_to_delete
    log_info(f"Disimpan {len(all_messages_to_delete)} ID pesan hasil untuk kunci command: {request_key_command}")


async def tebak_angka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk memulai permainan tebak angka."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    username = update.message.from_user.first_name
        
    # 1. Cek dan hapus game lama jika user yang sama memulai lagi
    if chat_id in active_games:
        prev_game = active_games[chat_id]
            
        if prev_game['starter_id'] == user_id:
            if prev_game.get('delete_timer'):
                prev_game['delete_timer'].cancel()
            
            # Panggil fungsi deletion (akan menggunakan create_task karena delay=0)
            handle_game_deletion(context, chat_id, prev_game['message_id'], delay=0)
            del active_games[chat_id]
            log_info(f"Permainan lama dihapus segera oleh {username}.")

        else:
            # Peringatan jika user lain mencoba memulai
            try:
                starter_member = await context.bot.get_chat_member(chat_id, prev_game['starter_id']) 
                starter_name = starter_member.user.first_name
            except Exception:
                starter_name = "User Lain"
                
            # --- Perubahan dimulai di sini ---
            warning_msg = await update.message.reply_text(
                f"⚠️ Maaf, **{username}**, ada permainan yang sedang berlangsung oleh **{starter_name}**!",
                parse_mode="Markdown"
            )
            
            app_bot = context.application.bot 
            app_loop = context.application.loop
            msg_id = warning_msg.message_id
            
            # Coroutine untuk menghapus pesan peringatan
            async def delete_warning_async():
                try:
                    await app_bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    log_info(f"Pesan peringatan tebak angka di chat {chat_id} (ID: {msg_id}) dihapus otomatis (10 detik).")
                except Exception:
                    pass
                
            # Fungsi thread-safe untuk menjadwalkan penghapusan
            def schedule_delete():
                if app_loop.is_running():
                    asyncio.run_coroutine_threadsafe(delete_warning_async(), app_loop)

            # Start the timer untuk menghapus pesan setelah 10 detik
            threading.Timer(10, schedule_delete).start()
            # --- Perubahan berakhir di sini ---
            
            return

    # 2. Inisialisasi Game Baru
    target_number = random.randint(1, 10)
    attempts = 3

    new_game_state = {
        'target': target_number,
        'attempts_left': attempts,
        'starter_id': user_id,
        'message_id': None, 
        'delete_timer': None
    }
        
    log_info(f"Permainan Tebak Angka dimulai oleh {username}. Target: {target_number}")

    # 3. Buat Tombol
    keyboard = []
    row1 = [InlineKeyboardButton(str(i), callback_data=f"guess|{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"guess|{i}") for i in range(6, 11)]
    keyboard.append(row1)
    keyboard.append(row2)
    reply_markup = InlineKeyboardMarkup(keyboard)

    # 4. Kirim Pesan Awal
    msg = await update.message.reply_text(
        f"🎯 <b>Tebak Angka 1 sampai 10!</b>\n"
        f"Pemain: {username}\n"
        f"Kesempatanmu: <b>{attempts} kali</b>.\n"
        f"Pilih angka di bawah ini:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    # 5. Simpan State dan Atur Timer 30s
    new_game_state['message_id'] = msg.message_id
    active_games[chat_id] = new_game_state
        
    app_bot = context.application.bot 
    app_loop = context.application.loop
        
    # Coroutine yang akan dijalankan di event loop
    async def delete_async_coro():
        try:
            await app_bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            log_info(f"Permainan Tebak Angka di chat {chat_id} dihapus otomatis (30 detik timeout).")
        except Exception:
            pass
        finally:
            if chat_id in active_games and active_games[chat_id]['message_id'] == msg.message_id:
                del active_games[chat_id]
                
    # Fungsi thread-safe yang dipanggil oleh threading.Timer
    def cleanup_unanswered_game():
        if chat_id in active_games and active_games[chat_id]['message_id'] == msg.message_id:
            if app_loop.is_running():
                asyncio.run_coroutine_threadsafe(delete_async_coro(), app_loop)
                
    timer_30s = threading.Timer(30, cleanup_unanswered_game)
    timer_30s.start()
    active_games[chat_id]['delete_timer'] = timer_30s

# ======== MESSAGE AND CALLBACK HANDLERS ========
async def greet_on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menyambut bot saat bergabung ke grup."""
    new_members = update.message.new_chat_members
    bot_id = context.bot.id
    chat_id = update.message.chat_id
    chat_type = update.message.chat.type

    for member in new_members:
        if member.id == bot_id:
            if chat_type in ["group", "supergroup"]:
                global LOADED_CHAT_IDS
                if chat_id not in LOADED_CHAT_IDS:
                    LOADED_CHAT_IDS.add(chat_id)
                    save_chat_ids()
                    log_success(f"New group chat ID added upon join: {chat_id}")

            log_info(f"Bot joined chat {chat_id}. Sending trigger 'Vee'.")
            await update.message.reply_text("Vee")
            return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pesan teks biasa, termasuk trigger Gemini ('Vee')."""
    text = update.message.text
        
    prompt_text = text
    # Hapus mention bot (@username) jika ada
    if update.message.chat.type in ["group", "supergroup"] and context.bot.username:
        bot_mention = f"@{context.bot.username}"
        prompt_text = re.sub(re.escape(bot_mention), '', prompt_text, flags=re.IGNORECASE).strip()
        
    if "vee" in prompt_text.lower():
        log_info(f"Chat Gemini dipanggil: {text}")
            
        cleaned = prompt_text.lower().replace("vee", "", 1).strip() or "Halo!"
            
        # Panggil Gemini AI (Blocking call, jalankan di executor)
        reply = await asyncio.get_event_loop().run_in_executor(None, chat_gemini, cleaned)
            
        await update.message.reply_text(reply)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pesan suara (transkripsi)."""
    log_info("Pesan suara diterima.")
        
    def voice_to_text_blocking():
        file = context.bot.get_file(update.message.voice.file_id)
        # Operasi I/O blocking
        file.download("voice.ogg") 
        sound = AudioSegment.from_file("voice.ogg", format="ogg")
        sound.export("voice.wav", format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile("voice.wav") as source:
            audio_data = recognizer.record(source)
            # Operasi I/O/CPU blocking
            text = recognizer.recognize_google(audio_data, language="id-ID") 
        return text

    try:
        # Jalankan I/O blocking di executor
        text = await asyncio.get_event_loop().run_in_executor(None, voice_to_text_blocking)
        await update.message.reply_text(f"🎙️ Kamu bilang: {text}")
    except Exception as e:
        log_error(f"Gagal mengenali suara: {e}")
        await update.message.reply_text("⚠️ Maaf, gagal memproses pesan suara.")
    finally:
        # Bersihkan file
        if os.path.exists("voice.ogg"):
            os.remove("voice.ogg")
        if os.path.exists("voice.wav"):
            os.remove("voice.wav")

# --- FUNGSI button DIUBAH UNTUK MENGAMBIL URL DARI DICTIONARY GLOBAL ---
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk semua callback button."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    current_user_id = query.from_user.id
    username = query.from_user.first_name

    # --- Logika Tebak Angka ---
    if data.startswith("guess|"):
        # Logika tebak angka (Tidak ada perubahan)
        if chat_id not in active_games:
            try:
                await query.edit_message_text("Permainan tebak angka ini sudah berakhir atau tidak ditemukan.")
            except:
                pass 
            return

        game = active_games[chat_id]
            
        if current_user_id != game['starter_id']:
            await query.answer("🚫 Ini bukan permainanmu! Mohon mulai permainanmu sendiri dengan /tebakangka.")
            return
            
        if game.get('delete_timer'):
            game['delete_timer'].cancel()
            game['delete_timer'] = None
            
        guessed_number = int(data.split("|")[1])
        game['attempts_left'] -= 1
            
        log_info(f"Tebakan: {guessed_number} dari {username}. Sisa: {game['attempts_left']}x. Target: {game['target']}")
            
        message_text = f"🎯 <b>Tebak Angka 1 sampai 10!</b>\n"
        message_text += f"Pemain: {username}\n"
        message_text += f"Tebakan terakhirmu: <b>{guessed_number}</b>\n\n"
        reply_markup = None
            
        if guessed_number == game['target'] or game['attempts_left'] == 0:
            # Game SELESAI (Menang/Kalah)
            msg_id_to_delete = query.message.message_id
                
            app_bot = context.application.bot 
            app_loop = context.application.loop
                
            async def cleanup_finished_game_async():
                try:
                    await app_bot.delete_message(chat_id=chat_id, message_id=msg_id_to_delete)
                except Exception:
                    pass
                if chat_id in active_games:
                    del active_games[chat_id]

            def cleanup_finished_game_threadsafe():
                if app_loop.is_running():
                    asyncio.run_coroutine_threadsafe(cleanup_finished_game_async(), app_loop)

            threading.Timer(6, cleanup_finished_game_threadsafe).start()
                
            if guessed_number == game['target']:
                message_text += f"🎉 <b>BENAR!</b> Angkanya adalah <b>{game['target']}</b>.\n"
                message_text += f"Hebat! Kamu berhasil dalam {3 - game['attempts_left']} kali coba."
                log_success(f"Game dimenangkan oleh {username}.")

            else:
                message_text += f"❌ <b>SAYANG SEKALI!</b> Kesempatanmu sudah habis.\n"
                message_text += f"Angka yang benar adalah <b>{game['target']}</b>."
                log_warn(f"Game berakhir. Kalah.")
                
        else:
            # LANJUT
            msg_id_to_edit = query.message.message_id
            app_bot = context.application.bot
            app_loop = context.application.loop

            async def cleanup_unanswered_game_retry_async():
                if chat_id in active_games and active_games[chat_id]['message_id'] == msg_id_to_edit:
                    try:
                        await app_bot.delete_message(chat_id=chat_id, message_id=msg_id_to_edit)
                    except Exception:
                        pass
                    finally:
                        if chat_id in active_games:
                            del active_games[chat_id]
                    
            def cleanup_unanswered_game_retry_threadsafe():
                if app_loop.is_running():
                    asyncio.run_coroutine_threadsafe(cleanup_unanswered_game_retry_async(), app_loop)
                        
            timer_30s_retry = threading.Timer(30, cleanup_unanswered_game_retry_threadsafe)
            timer_30s_retry.start()
            active_games[chat_id]['delete_timer'] = timer_30s_retry
                    
            keyboard = []
            row1 = [InlineKeyboardButton(str(i), callback_data=f"guess|{i}") for i in range(1, 6)]
            row2 = [InlineKeyboardButton(str(i), callback_data=f"guess|{i}") for i in range(6, 11)]
            keyboard.append(row1)
            keyboard.append(row2)
            reply_markup = InlineKeyboardMarkup(keyboard)

            hint = "LEBIH TINGGI ⬆️" if guessed_number < game['target'] else "LEBIH RENDAH ⬇️"
            message_text += f"Petunjuk: <b>{hint}</b>\n"
            message_text += f"Sisa kesempatan: <b>{game['attempts_left']} kali</b>."
                    
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            log_error(f"Gagal mengedit pesan game tebak angka: {e}")
            
        return

    # --- Logika Download Lagu ---
    if data.startswith("dl|"):
        parts = data.split("|", 2) # Format: dl|ResultMsgID|RequestMsgID
        
        if len(parts) != 3:
            await query.answer("⚠️ Data tombol tidak valid.")
            return

        result_msg_id = int(parts[1])
        request_msg_id = int(parts[2])
        
        request_key_result = f"{chat_id}_{result_msg_id}"
        request_key_command = f"{chat_id}_{request_msg_id}"
        
        global youtube_requests

        # 1. Ambil Data URL dan Requestor ID dari dictionary (kunci pesan hasil)
        # Jika data pesan hasil tidak ada, itu berarti pesan sudah kedaluwarsa atau server restart
        request_data = youtube_requests.get(request_key_result)

        if not request_data:
            await query.answer("⚠️ Sesi permintaan ini telah berakhir. Silakan ulangi /lagu.")
            
            # Coba hapus pesan yang diklik jika data permintaan tidak ditemukan
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
            except Exception:
                pass
                
            return
            
        url = request_data['url']
        requestor_id = request_data['requestor_id']
        
        if current_user_id != requestor_id:
            await query.answer("ini bukan request kamu, tombol tidak berfungsi", show_alert=True)
            return

        log_info(f"Download Song diminta: {url} (Kunci: {request_key_result})")

        # 2. HAPUS SEMUA PESAN HASIL PENCARIAN
        # Ambil daftar ID pesan yang akan dihapus (dari kunci pesan command)
        messages_to_delete = youtube_requests.pop(request_key_command, [])
        
        # Hapus data pesan hasil yang diklik dari dictionary (kunci pesan hasil)
        # Hapus semua kunci pesan hasil yang memiliki request_msg_id yang sama
        keys_to_delete_result = [key for key, data in youtube_requests.items() if data.get('request_msg_id') == request_msg_id]
        for key in keys_to_delete_result:
             youtube_requests.pop(key, None)

        if messages_to_delete:
            context.application.create_task(delete_messages_async(context, chat_id, messages_to_delete))
            log_info(f"Memulai penghapusan {len(messages_to_delete)} pesan hasil untuk kunci command: {request_key_command}")

        # Kirim status "Downloading..." (Pesan ini harus dihapus pada akhirnya)
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="💿 <b>Downloading...</b>",
            parse_mode="HTML"
        )
            
        # Variabel untuk menyimpan title
        title = "Lagu Tanpa Judul"
            
        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": "song.%(ext)s",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "192",
                }],
                "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
                "quiet": False,
                # PERBAIKAN TIMEOUT YTDLP (dari langkah sebelumnya)
                "socket_timeout": 300, # 5 menit untuk operasi jaringan yt_dlp
            }
                
            def download_blocking():
                """Fungsi blocking untuk mengunduh lagu."""
                nonlocal title # Gunakan nonlocal agar bisa mengubah variabel title di luar fungsi
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Ambil info sebelum download dimulai
                    info = ydl.extract_info(url, download=False)
                    title = info.get("title", "Lagu Tanpa Judul")
                    ydl.download([url])
                return title

            # Jalankan operasi blocking di executor
            title = await asyncio.get_event_loop().run_in_executor(None, download_blocking)
                
            # Update status ke 'Sending File...'
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="📤 <b>Sending File...</b>",
                parse_mode="HTML"
            )

            # Kirim Audio
            if os.path.exists("song.m4a"):
                # --- PERBAIKAN: Tambahkan read_timeout untuk upload file besar ---
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=open("song.m4a", "rb"), # Menggunakan open() di sini (Blocking IO)
                    title=f"🎵 {title}",
                    caption=f"<b>✨ Done...Enjoy 🎵</b>",
                    parse_mode="HTML",
                    read_timeout=300 # Tambahkan timeout 5 menit untuk upload file besar
                )
                # -------------------------------------------------------------------
                
            log_success(f"Lagu '{title}' berhasil dikirim ✅")

        except Exception as e:
            # Jika terjadi error (termasuk upload timeout), tangkap di sini
            log_error(f"Gagal mengunduh/mengirim lagu: {e}")
            
        finally:
            # BLOCK FINALLY: Pastikan pesan status dihapus!
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
            except Exception:
                pass
                
            # Pastikan file lokal dihapus, terlepas dari apakah pengiriman berhasil atau gagal
            if os.path.exists("song.m4a"):
                try:
                    os.remove("song.m4a")
                except Exception as e:
                    log_error(f"Gagal menghapus file lokal song.m4a: {e}")
            
# ======== ERROR HANDLER ========
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler untuk menangkap semua error bot."""
    log_error(f"Terjadi error: {context.error}")
    try:
        if update and hasattr(update, "message") and update.message:
            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text="⚠️ Maaf, terjadi kesalahan pada bot."
            )
    except Exception as e:
        log_error(f"Gagal handle error: {e}")

# ======== MAIN FUNCTION ========
def main():
    """Fungsi utama untuk menjalankan bot."""
    if not TELEGRAM_TOKEN:
        log_error("TELEGRAM_TOKEN tidak ditemukan di file .env. Bot gagal dijalankan.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    dp = application 
    
    # Simpan loop untuk akses thread-safe di Game/Timer dan Watchdog
    application.loop = asyncio.get_event_loop() 

    # Load data
    load_chat_ids()
    
    # Scheduled Job (Hanya berjalan jika JobQueue diinstal)
    job_queue = application.job_queue
    if job_queue:
        # Jalankan Fakta Dunia setiap 30 menit (1800 detik)
        job_queue.run_repeating(send_scheduled_fact, interval=1800, first=1800) 
        log_success("JobQueue Fakta Dunia Terjadwal diaktifkan.")
        
        # --- Mulai Watchdog Auto-Restart ---
        start_watchdog(application)
        # --- Akhir Watchdog --
    
    else:
        log_warn("JobQueue tidak diinstal. Fitur terjadwal tidak akan berjalan.")
        
    # Perbaikan: Menambahkan filters.ChatType.GROUPS | filters.ChatType.PRIVATE agar command
    # lebih mudah dikenali di grup dan private chat.
    group_or_private_filter = filters.ChatType.GROUPS | filters.ChatType.PRIVATE

    # Command Handlers
    dp.add_handler(CommandHandler("start", start, filters=group_or_private_filter))
    dp.add_handler(CommandHandler("lagu", lagu, filters=group_or_private_filter))
    dp.add_handler(CommandHandler("cek", cek, filters=group_or_private_filter))
    dp.add_handler(CommandHandler("uptime", uptime, filters=group_or_private_filter))
    dp.add_handler(CommandHandler("tebakangka", tebak_angka, filters=group_or_private_filter)) 
    
    # Message Handlers
    dp.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_on_join))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)) 
    dp.add_handler(MessageHandler(filters.VOICE, handle_voice)) 
    
    # Callback Handler
    dp.add_handler(CallbackQueryHandler(button))
    
    # Error Handler
    dp.add_error_handler(error_handler)

    log_success("Vee aktif! dan siap menerima perintah 🚀")
    
    application.run_polling(poll_interval=0.5)

if __name__ == "__main__":
    main()
