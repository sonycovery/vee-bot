import os
import asyncio
import requests
import yt_dlp
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# ==== GANTI DENGAN TOKEN KAMU ====
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
STABILITY_API_KEY = os.environ.get("STABILITY_KEY")
GENIUS_ACCESS_TOKEN = os.environ.get("GENIUS_ACCESS_TOKEN")
# =================================

song_data = {}

# ==== CHAT GEMINI ====
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

# ==== START ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! 😎 Aku Vee.\n"
        "Kamu bisa chat, atau minta lagu 🎵\n"
        "Coba aja sebut namaku biar aku jawab 😉"
    )

# ==== CEK ====    
async def cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cek apa?! 😠\nSaya tidak diperbolehkan tidur!")

# ==== GET YOUTUBE INFO ====
def get_youtube_info(query):
    ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        if 'entries' in result and len(result['entries']) > 0:
            return result['entries'][0]
        else:
            return None

# ==== GET LYRICS URL ====
def get_lyrics_from_genius(title):
    headers = {"Authorization": f"Bearer {GENIUS_ACCESS_TOKEN}"}
    search_url = f"https://api.genius.com/search?q={title}"
    res = requests.get(search_url, headers=headers)
    if res.status_code == 200:
        hits = res.json().get("response", {}).get("hits", [])
        if hits:
            song_path = hits[0]["result"]["path"]
            return f"https://genius.com{song_path}"
        else:
            return None
    else:
        return None

# ==== LAGU COMMAND ====
async def lagu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Format salah 😅\nCoba: /lagu Tulus - Hati-hati di Jalan")
        return

    query_text = ' '.join(context.args)
    print(f"📩 User mencari lagu: {query_text}")  # log chat

    await update.message.reply_text(f"🎧 Sedang mencari lagu: {query_text} ...")

    info = get_youtube_info(query_text)
    if not info:
        await update.message.reply_text("Waduh, lagunya gak ketemu 😔")
        return
        
    song_id = info.get("id")
    song_data[song_id] = {
    	"title": info.get("title", "Lagu Tidak Diketahui"),
    	"url": f"https://www.youtube.com/watch?v={song_id}"
    }

    title = info.get("title", "Tidak diketahui")
    url = f"https://www.youtube.com/watch?v={info.get('id')}"
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail")
    mins, secs = divmod(duration, 60)
    duration_str = f"{int(mins)}:{int(secs):02d}" if duration else "?"

    lyrics_url = get_lyrics_from_genius(title)

    text = (f"🎵 <b>{title}</b>\n"
            f"⏱️ Durasi: {duration_str}\n"
            f"📺 <a href='{url}'>Tonton di YouTube</a>")

    # Buat tombol jika ada lirik
    if lyrics_url:
        keyboard = [
            [InlineKeyboardButton("▶️ Preview 30 detik", callback_data=f"preview|{song_id}")],
            [InlineKeyboardButton("⬇️ Unduh Lagu Full", callback_data=f"download|{song_id}")],
            [InlineKeyboardButton("📜 Lyrics", callback_data=f"lyrics|{song_id}")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("▶️ Preview 30 detik", callback_data=f"preview|{song_id}")],
            [InlineKeyboardButton("⬇️ Unduh Lagu Full", callback_data=f"download|{song_id}")]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if thumbnail:
        await update.message.reply_photo(photo=thumbnail, caption=text,
                                         reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

# ==== DOWNLOAD FULL SONG (ASYNC) ====
async def download_full_song(url, chat_id, bot, song_title="Lagu"):
    status_msg = await bot.send_message(chat_id, "⬇️ Mulai mengunduh lagu full... 0%")
    loop = asyncio.get_running_loop()
    last_percent = {'value': -1}  # simpan progress terakhir

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            if total:
                percent = int(downloaded / total * 100)
                if percent != last_percent['value']:
                    last_percent['value'] = percent
                    try:
                        loop.call_soon_threadsafe(asyncio.create_task,
                            status_msg.edit_text(f"⬇️ Mengunduh lagu full... {percent}%"))
                    except Exception:
                        pass
        elif d['status'] == 'finished':
            try:
                loop.call_soon_threadsafe(asyncio.create_task,
                    status_msg.edit_text("✅ Download selesai, mengirim file ke Telegram..."))
            except Exception:
                pass

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": "song.%(ext)s",
        "postprocessors": [{"key": "FFmpegExtractAudio","preferredcodec": "mp3","preferredquality": "192"}],
        "progress_hooks": [progress_hook],
        "quiet": True
    }

    await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).download([url]))

    if os.path.exists("song.mp3"):
        await bot.send_audio(chat_id, audio=open("song.mp3","rb"),
                             title=f"🎵 {song_title}", caption="✅ Selamat menikmati lagunya! 🎧")
        os.remove("song.mp3")
    else:
        await status_msg.edit_text("⚠️ Gagal ngunduh lagu, coba lagi ya.")
        
# ==== GENERATE FOTO (Stability.ai SD XL v2) ====
async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text(
            "⚠️ Masukkan deskripsi/gambar, contoh: /gambar kucing lucu di taman"
        )
        return

    prompt = ' '.join(context.args)
    await update.message.reply_text(f"🖌️ Sedang membuat gambar dari: {prompt} ...")

    import requests, base64, os
    from io import BytesIO
    from PIL import Image

    STABILITY_API_KEY = "YOUR_STABILITY_API_KEY"  # ganti dengan API Key Anda
    model_id = "stable-diffusion-xl-beta-v2-2"    # model SD XL v2

    url = f"https://api.stability.ai/v2beta/generation/{model_id}/text-to-image"

    # multipart/form-data
    data = {
        "text_prompts[0][text]": prompt,
        "cfg_scale": 7,
        "height": 512,
        "width": 512,
        "samples": 1,
        "steps": 30
    }

    headers = {
        "Authorization": f"Bearer {STABILITY_API_KEY}"
    }

    try:
        res = requests.post(url, headers=headers, data=data)
        if res.status_code == 200:
            result = res.json()
            img_base64 = result["artifacts"][0]["base64"]
            image_bytes = base64.b64decode(img_base64)
            image = Image.open(BytesIO(image_bytes))
            image.save("temp.png")

            await update.message.reply_photo(
                photo=open("temp.png", "rb"),
                caption=f"✨ Gambar untuk: {prompt}"
            )
            os.remove("temp.png")
        else:
            await update.message.reply_text(
                f"⚠️ Gagal generate gambar: {res.status_code} {res.text}"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ==== CALLBACK BUTTON ====
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    print(f"📩 Callback data diterima: {data}")  # log

    # Hanya hapus pesan jika interaksi terkait lagu
    if any(data.startswith(prefix) for prefix in ["preview|", "download|", "lyrics|"]):
        try:
            await query.message.delete()  # hapus pesan sebelumnya
        except:
            pass  # kalau gagal dihapus, abaikan

    if data.startswith("select_song|"):
        video_id = data.split("|", 1)[1]
        url = f"https://www.youtube.com/watch?v={video_id}"
        info = get_youtube_info(url)
        if info:
            await send_song_info(update, context, info)

    elif data.startswith("preview|"):
        song_id = data.split("|", 1)[1]
        info = song_data.get(song_id)
        if not info:
        	await query.message.reply_text("⚠️ Data lagu tidak tersedia.")
        	return
        url = info["url"]
        title = info["title"]
        
        # Kirim info preview seperti sebelumnya
        await query.message.reply_text("🎧 Lagi nyiapin preview 30 detik... bentar ya~")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": "preview.%(ext)s",
            "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"128"}],
            "quiet": True,
        }
        try:
            def download_preview():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    duration = info.get("duration",0)
                    if duration > 30:
                        ydl_opts["postprocessor_args"] = ["-ss", "0", "-t", "30"]
                    ydl.download([url])

            await asyncio.to_thread(download_preview)

            preview_file = "preview.mp3"
            if os.path.exists(preview_file):
                keyboard = [[InlineKeyboardButton("⬇️ Unduh Lagu Full", callback_data=f"download|{song_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_audio(chat_id=query.message.chat.id,
                                             audio=open(preview_file,"rb"),
                                             title="🎶 Preview Lagu (30 detik)",
                                             caption="✨ Cuplikannya udah jadi! Mau download full gak? 😄",
                                             reply_markup=reply_markup)
                os.remove(preview_file)
            else:
                await query.message.reply_text("⚠️ Gagal buat preview, coba lagi ya.")

        except Exception as e:
            await query.message.reply_text(f"❌ Error waktu bikin preview: {e}")

    elif data.startswith("download|"):
        song_id = data.split("|", 1)[1]
        info = song_data.get(song_id)
        if not info:
        	await query.message.reply_text("⚠️ Data lagu tidak tersedia.")
        	return
        url = info["url"]
        title = info["title"]
        await download_full_song(url, query.message.chat.id, context.bot, song_title=title)

    elif data.startswith("lyrics|"):
        song_id = data.split("|",1)[1]
        info = song_data.get(song_id)
        if not info:
        	await query.message.reply_text("⚠️ Data lagu tidak tersedia.")
        	return
        lyrics_url = get_lyrics_from_genius(info["title"])
        if lyrics_url:
            await query.message.reply_text(f"📜 Lirik untuk <b>{info['title']}</b>:\n{lyrics_url}", parse_mode="HTML")
        else:
            await query.message.reply_text("⚠️ Lirik tidak ditemukan.")


# ==== TEKS CHAT ====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    print(f"📩 Chat dari {update.message.from_user.username or update.message.from_user.id}: {text}")  # log
    if "vee" in text.lower():
        cleaned = text.lower().replace("vee","").strip()
        if not cleaned: cleaned = "Halo!"
        reply = chat_gemini(cleaned)
        await update.message.reply_text(reply)
        
# ==== SERVER KECIL UNTUK UPTIMEROBOT ====
app_flask = Flask("")

@app_flask.route("/")
def home():
    return "Vee Bot is alive!"

def run():
    app_flask.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()


# ==== MAIN ====
from telegram import BotCommand

async def set_commands(app):
    bot_commands = [
        BotCommand("start", "Memulai bot dan menampilkan pesan sambutan"),
        BotCommand("cek", "Cek bot online atau tidur"),
        BotCommand("lagu", "Mencari lagu di YouTube, contoh: /lagu Tulus - Hati-hati di Jalan"),
        BotCommand("gambar", "Generate gambar dari teks, contoh: /gambar kucing lucu di taman"),
    ]
    await app.bot.delete_my_commands()
    await app.bot.set_my_commands(bot_commands)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Daftarkan handler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cek", cek))
    app.add_handler(CommandHandler("lagu", lagu))
    app.add_handler(CommandHandler("gambar", generate_image))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    # Set post_init sebagai coroutine
    app.post_init = set_commands
    
    # 🔹 START FLASK UNTUK UPTIMEROBOT
    keep_alive()

    print("✅ Vee aktif! Menunggu chat...")
    app.run_polling()  # run_polling sudah menangani loop asyncio sendiri

if __name__ == "__main__":
    main()


