import requests
import yt_dlp
import os
import time
import random
import speech_recognition as sr
from dotenv import load_dotenv
from pydub import AudioSegment
from gtts import gTTS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext

load_dotenv()

# ==== GANTI DENGAN TOKEN KAMU ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
# =================================

# ==== FUNGSI CHAT GEMINI ====
def chat_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    res = requests.post(url, json=data)
    if res.status_code == 200:
        try:
            return res.json()["candidates"][0]["content"]["parts"][0]["text"]
        except:
            return "⚠️ Aku agak bingung jawabnya nih."
    else:
        return f"⚠️ Error dari server Gemini: {res.text}"

# ==== GAYA SANTAI ====
def gaya_santai(text):
    kata_pembuka = random.choice([
        "Hehe, gini nih jawabannya 👇",
        "Oke deh, jadi gini ya...",
        "Hmm... kalau menurutku sih begini:",
        "Santai aja, nih aku jelasin ya:",
        "Oke nih bro, ini infonya:"
    ])
    kata_penutup = random.choice([
        "Gimana, jelas kan? 😄",
        "Semoga ngebantu yaa ✌️",
        "Keren kan? 😎",
        "Sip deh, gitu kira-kira.",
        "Udah paham ya? 😉"
    ])
    return f"{kata_pembuka}\n\n{text}\n\n{kata_penutup}"

# ==== TEXT KE SUARA ====
def text_to_speech(text, filename="reply.mp3"):
    tts = gTTS(text=text, lang='id')
    tts.save(filename)
    return filename

# ==== START ====
def start(update, context):
    update.message.reply_text(
        "Halo! 😎 Aku Vee.\n"
        "Kamu bisa chat, kirim voice, atau minta lagu 🎵\n"
        "Coba aja sebut namaku biar aku jawab 😉"
    )

# ==== INFO LAGU ====
def get_youtube_info(query):
    ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        if 'entries' in result and len(result['entries']) > 0:
            return result['entries'][0]
        else:
            return None

# ==== FUNGSI UNTUK MENDAPATKAN URL LIRIK ====
def get_lyrics_url(title):
    import urllib.parse
    base_url = "https://genius.com/search?q="
    query = urllib.parse.quote(title)
    return f"{base_url}{query}"

# ==== FUNGSI /LAGU ====
def lagu(update, context):
    if len(context.args) == 0:
        update.message.reply_text(
            "Format salah 😅\nCoba: /lagu Tulus - Hati-hati di Jalan"
        )
        return

    query_text = ' '.join(context.args)
    update.message.reply_text(f"🎧 Sedang mencari lagu: {query_text} ...")

    info = get_youtube_info(query_text)
    if not info:
        update.message.reply_text("Waduh, lagunya gak ketemu 😔")
        return

    title = info.get("title", "Tidak diketahui")
    url = f"https://www.youtube.com/watch?v={info.get('id')}"
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail")
    mins, secs = divmod(duration, 60)
    duration_str = f"{int(mins)}:{int(secs):02d}" if duration else "?"

    text = (f"🎵 <b>{title}</b>\n"
            f"⏱️ Durasi: {duration_str}\n"
            f"📺 <a href='{url}'>Tonton di YouTube</a>")

    keyboard = [
        [InlineKeyboardButton("▶️ Preview 30 detik", callback_data=f"preview|{url}")],
        [InlineKeyboardButton("⬇️ Unduh Lagu Full", callback_data=f"download|{url}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if thumbnail:
        update.message.reply_photo(photo=thumbnail, caption=text,
                                   reply_markup=reply_markup, parse_mode="HTML")
    else:
        update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

# ==== CALLBACK (Preview/Download) ====
def button(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    print(f"📩 Callback data diterima: {data}")

    url = data.split("|", 1)[1]

    # hapus pesan tombol awal supaya tombol tidak menumpuk
    try:
        query.message.delete()
    except Exception as e:
        print(f"⚠️ Gagal hapus pesan awal: {e}")

    # --- Buat keyboard umum supaya tombol tetap ada ---
    url = data.split("|", 1)[1]
    keyboard = [
        [InlineKeyboardButton("⬇️ Unduh Lagu Full", callback_data=f"download|{url}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # --- Preview 30 detik ---
    if data.startswith("preview|"):
        print(f"▶️ Memproses preview: {url}")
        query.message.reply_text("🎧 Lagi nyiapin preview 30 detik... bentar ya~")

        try:
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": "preview.%(ext)s",
                "prefer_ffmpeg": True,
                "source_address": "0.0.0.0",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "128",
                }],
                "quiet": False,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                duration = info.get("duration", 0)
                if duration > 30:
                    ydl_opts["postprocessor_args"] = ["-ss", "0", "-t", "30"]
                ydl.download([url])

            preview_file = "preview.m4a"
            if os.path.exists(preview_file):
                context.bot.send_audio(
                    chat_id=query.message.chat.id,
                    audio=open(preview_file, "rb"),
                    title="🎶 Preview Lagu (30 detik)",
                    caption="✨ Cuplikannya udah jadi! Mau download full gak? 😄",
                    reply_markup=reply_markup  # tombol tetap ada
                )
                os.remove(preview_file)
            else:
                query.message.reply_text("⚠️ Gagal buat preview, coba lagi ya.",
                                         reply_markup=reply_markup)

        except Exception as e:
            query.message.reply_text(f"❌ Error waktu bikin preview: {e}",
                                     reply_markup=reply_markup)
            print(f"🔥 ERROR saat preview: {e}")

    # --- Download Full Lagu ---
    elif data.startswith("download|"):
        print(f"⬇️ Memproses download full: {url}")
        status_msg = query.message.reply_text("⬇️ Mulai mengunduh lagu... 0%")

        def progress_hook(d):
            if d['status'] == 'downloading':
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
                downloaded = d.get('downloaded_bytes',0)
                if total_bytes:
                    percent = int(downloaded/total_bytes*100)
                    try:
                        status_msg.edit_text(f"⬇️ Mengunduh... {percent}%")
                    except: pass
            elif d['status'] == 'finished':
                try:
                    status_msg.edit_text("✅ Download selesai, mengirim file ke Telegram...")
                except: pass

        try:
            ydl_opts = {
                "format":"bestaudio/best",
                "outtmpl":"song.%(ext)s",
                "prefer_ffmpeg": True,
                "source_address": "0.0.0.0",
                "postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"m4a","preferredquality":"192"}],
                "progress_hooks":[progress_hook],
                "quiet": False,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            }

            if os.path.exists("song.m4a"): os.remove("song.m4a")
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                video_title = info.get("title", "Lagu Tanpa Judul")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            if os.path.exists("song.m4a"):
                context.bot.send_audio(chat_id=query.message.chat.id,
                                       audio=open("song.m4a","rb"),
                                       title=f"🎵 {video_title}",
                                       caption=f"✅ {video_title}\n\nSelamat menikmati lagunya! 🎧",
                                       timeout=300)
                os.remove("song.m4a")
            else:
                status_msg.edit_text("⚠️ Gagal ngunduh lagu, coba lagi ya.")

        except Exception as e:
            status_msg.edit_text(f"❌ Error waktu unduh lagu: {e}")
            print(f"🔥 ERROR download: {e}")

# ==== TEKS & VOICE ====
def handle_text(update, context):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    if "vee" in text.lower():
        cleaned = text.lower().replace("vee", "").strip()
        if not cleaned:
            cleaned = "Halo!"
        reply = chat_gemini(cleaned)  # ambil jawaban dari Gemini
        update.message.reply_text(reply)  # kirim langsung, tanpa styled


def handle_voice(update, context):
    file = context.bot.getFile(update.message.voice.file_id)
    file.download("voice.ogg")
    try:
        sound = AudioSegment.from_file("voice.ogg", format="ogg")
        sound.export("voice.wav", format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile("voice.wav") as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="id-ID")
        update.message.reply_text(f"🎙️ Kamu bilang: {text}")
        if "vee" in text.lower():
            cleaned = text.lower().replace("vee","").strip()
            reply = chat_gemini(cleaned)
            styled = gaya_santai(reply)
            update.message.reply_text(styled)
            tts_file = text_to_speech(styled)
            update.message.reply_voice(voice=open(tts_file,"rb"))
            os.remove(tts_file)
        else:
            update.message.reply_text("✨ Sebut 'Vee' biar aku jawab ya 😄")
    except Exception as e:
        update.message.reply_text(f"⚠️ Gagal mengenali suara: {e}")
    finally:
        for f in ["voice.ogg","voice.wav"]:
            if os.path.exists(f): os.remove(f)

# ==== MAIN ====
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("lagu", lagu))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    dp.add_handler(MessageHandler(Filters.update.edited_message & Filters.text, handle_text))
    dp.add_handler(MessageHandler(Filters.voice, handle_voice))
    dp.add_handler(CallbackQueryHandler(button))

    print("✅ Vee aktif! Bot siap menerima perintah.")
    
    while True:
        try:
            updater.start_polling(timeout=15, drop_pending_updates=True)
            updater.idle()
        except KeyboardInterrupt:
            print("🛑 Bot dimatikan manual.")
            break
        except Exception as e:
            print(f"⚠️ Terjadi error jaringan / Telegram API: {e}")
            print("🔄 Mencoba reconnect dalam 5 detik...")
            time.sleep(5)
            continue

if __name__ == "__main__":
    main()
