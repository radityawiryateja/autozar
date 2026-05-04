import json
import logging
import re
import markdown
import os
import asyncio


from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, CallbackContext
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from supabase import create_client
import os
from telethon.sessions import StringSession

# Import Telethon untuk Userbot
from telethon import TelegramClient

# Tarik data dari Config Vars Heroku
try:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    CHANNEL_ID = os.getenv('CHANNEL_ID')
    GROUP_ID_DISKUSI = int(os.getenv('GROUP_ID_DISKUSI') or 0)
    ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID') or 0)
    LOG_GROUP_ID = int(os.getenv('LOG_GROUP_ID') or 0)
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')

    # === KREDENSIAL  TELETHON ===
    _API_ID = int(os.getenv('_API_ID') or 0)
    _API_HASH = os.getenv('_API_HASH')
    _PHONE = os.getenv('_PHONE')
    
    # Penarikan string session
    _SESSION = os.getenv('_SESSION_STRING')
except Exception as e:
    print(f"⚠️ Error mengambil Secrets dari Heroku: {e}")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

bot_active = True
MENFESS_MODE = "auto"

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal koneksi ke Supabase: {e}")

# Inisialisasi Client Telethon ()
 = TelegramClient(StringSession(_SESSION), _API_ID, _API_HASH)

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set()
CACHE_BAD_WORDS = set()

# ==========================================
# CACHE & STARTUP FUNCTIONS
# ==========================================
async def update_settings_cache():
    global MENFESS_MODE
    try:
        response = supabase.table("bot_settings").select("value").eq("key", "menfess_mode").execute()
        if hasattr(response, 'data') and response.data:
            MENFESS_MODE = response.data[0]["value"]
        else:
            supabase.table("bot_settings").insert({"key": "menfess_mode", "value": "auto"}).execute()
            MENFESS_MODE = "auto"
    except Exception as e: logger.error(f"Gagal memuat setting bot: {e}")

async def update_hashtags_cache():
    global CACHE_HASHTAGS
    try:
        response = supabase.table("triggered_hashtags").select("hashtag").eq("active", True).execute()
        CACHE_HASHTAGS = [row["hashtag"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e: logger.error(f"Gagal memuat cache hashtag: {e}")

async def update_badwords_cache():
    global CACHE_BAD_WORDS
    try:
        response = supabase.table("bad_words").select("word").execute()
        CACHE_BAD_WORDS = {row["word"].lower() for row in response.data} if hasattr(response, 'data') and response.data else set()
    except Exception as e: logger.error(f"Gagal memuat cache bad words: {e}")

async def update_required_channels_cache():
    global required_channels
    try:
        response = supabase.table('required_channels').select("channel_username").execute()
        required_channels = [row["channel_username"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e: logger.error(f"Gagal memuat required channels: {e}")

async def update_banned_users_cache():
    global CACHE_BANNED_USERS
    try:
        response = supabase.table('banned_users').select("user_id").execute()
        CACHE_BANNED_USERS = [row["user_id"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e: logger.error(f"Gagal memuat banned users: {e}")

async def on_startup(application: Application):
    try:
        me = await application.bot.get_me()
        logger.info(f"✅ Bot siap: @{me.username} (id={me.id})")
        await update_settings_cache()
        await update_hashtags_cache()
        await update_badwords_cache()
        await update_required_channels_cache()
        await update_banned_users_cache()

        # Mulai sesi Telethon saat bot startup
        await .start(phone=_PHONE)
        logger.info("✅  (Akun Asli) siap dan terhubung!")
    except Exception as e:
        logger.error(f"⚠️ Gagal get_me atau start  saat startup: {e}")

def save_required_channels(channels):
    try:
        supabase.table('required_channels').delete().neq("channel_username", "").execute()
        for channel in channels:
            supabase.table('required_channels').insert({"channel_username": channel}).execute()
    except Exception as e:
        logger.error(f"Gagal menyimpan required channels: {e}")

async def check_subscription(user_id, context: CallbackContext):
    if not required_channels: return True
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']: return False
        except Exception: return False
    return True

# ==========================================
# FITUR ADMIN (BADWORDS, BLOCK, MODE, HASHTAG)
# ==========================================
async def add_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2: return await update.message.reply_text("Format: /addbadwords kata1, kata2, kata3")
    words = [w.strip().lower() for w in raw_text[1].split(',')]
    inserted = 0
    for w in words:
        if w:
            try:
                supabase.table("bad_words").upsert({"word": w}).execute()
                inserted += 1
            except Exception: pass
    await update_badwords_cache()
    await update.message.reply_text(f"✅ {inserted} kata terlarang berhasil ditambahkan!")

async def remove_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    raw_text = update.message.text.split(maxsplit=1)
    if len(raw_text) < 2: return await update.message.reply_text("Format: /removebadwords kata1, kata2")
    words = [w.strip().lower() for w in raw_text[1].split(',')]
    deleted = 0
    for w in words:
        if w:
            try:
                supabase.table("bad_words").delete().eq("word", w).execute()
                deleted += 1
            except Exception: pass
    await update_badwords_cache()
    await update.message.reply_text(f"✅ {deleted} kata terlarang berhasil dihapus!")

async def list_badwords(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not CACHE_BAD_WORDS: return await update.message.reply_text("Daftar kata terlarang saat ini kosong.")
    word_list = ", ".join(sorted(CACHE_BAD_WORDS))
    await update.message.reply_text(f"🚫 *Daftar Kata Terlarang:*\n\n{word_list}", parse_mode="Markdown")

async def block_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /block <user_id>")
    try:
        target_id = int(context.args[0])
        supabase.table("banned_users").upsert({"user_id": target_id}).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil diblokir dari bot.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("❌ Gagal memblokir user. Pastikan format ID benar.")

async def unblock_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /unblock <user_id>")
    try:
        target_id = int(context.args[0])
        supabase.table("banned_users").delete().eq("user_id", target_id).execute()
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil di-unblock.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("❌ Gagal unblock user.")

async def set_mode_auto(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "auto"
    try: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "auto"}).execute()
    except Exception: pass
    await update.message.reply_text("✅ Mode menfess diubah ke *AUTO*. Menfess akan langsung terkirim ke channel.", parse_mode="Markdown")

async def set_mode_manual(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "manual"
    try: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "manual"}).execute()
    except Exception: pass
    await update.message.reply_text("⏸️ Mode menfess diubah ke *MANUAL*. Menfess akan masuk ke grup admin untuk direview terlebih dahulu.", parse_mode="Markdown")

async def add_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /addhashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").upsert({"hashtag": hashtag}).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` berhasil ditambahkan!", parse_mode="Markdown")

async def remove_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /removehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").delete().eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"❌ Hashtag `{hashtag}` berhasil dihapus!", parse_mode="Markdown")

async def enable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /enablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").update({"active": True}).eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` diaktifkan!", parse_mode="Markdown")

async def disable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /disablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    supabase.table("triggered_hashtags").update({"active": False}).eq("hashtag", hashtag).execute()
    await update_hashtags_cache()
    await update.message.reply_text(f"⚠️ Hashtag `{hashtag}` dinonaktifkan!", parse_mode="Markdown")

async def set_required_channels(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /setrequired @channel1 @channel2")
    global required_channels
    required_channels = context.args
    save_required_channels(required_channels)
    await update.message.reply_text(f"Daftar channel wajib diikuti telah diperbarui: {', '.join(required_channels)}")

# ==========================================
# FUNGSI PENCARIAN USERBOT (TELETHON)
# ==========================================
async def search_with_userbot(targets: list, channels: list):
    """Mencari list target (ID & Username) di daftar channel menggunakan akun asli."""
    found_links = []
    for ch in channels:
        for target in targets:
            if not target: continue
            try:
                async for message in userbot.iter_messages(ch, search=target, limit=2):
                    link = f"https://t.me/{ch}/{message.id}"
                    if link not in found_links:
                        found_links.append(link)
            except Exception as e:
                # Log error supaya ketahuan kalau ada limit/floodwait dari Telethon
                logger.error(f"Telethon error saat cari '{target}' di @{ch}: {e}")
    return found_links

async def check_penipu(update: Update, context: CallbackContext):
    target_id = None
    target_username = None

    # 1. PRIORITAS UTAMA: Cek apakah command ini me-reply sebuah pesan
    if update.message.reply_to_message:
        replied_msg = update.message.reply_to_message

        # Ekstrak ID dari pesan laporan/log jika dilakukan dari Grup Admin
        if update.effective_chat.id == ADMIN_GROUP_ID:
            replied_text = replied_msg.text or replied_msg.caption or ""
            match_id = re.search(r"ID:?\s*`?(\d+)`?", replied_text, re.IGNORECASE)
            
            # FIX: Tangkap "Username:" atau "Pengirim:" biar nggak Miss
            match_user = re.search(r"(?:Username|Pengirim):?\s*@?([a-zA-Z0-9_]+)", replied_text, re.IGNORECASE)

            if match_id: target_id = match_id.group(1)
            if match_user and match_user.group(1).lower() != 'none': target_username = f"@{match_user.group(1)}"

        # Jika bukan di grup admin (di comsect), atau gagal ekstrak dari format teks admin
        if not target_id and not target_username:
            target_id = str(replied_msg.from_user.id)
            if replied_msg.from_user.username: target_username = f"@{replied_msg.from_user.username}"

    # 2. Jika BUKAN reply pesan, baru baca teks setelah command (misal: /check @username)
    elif context.args:
        arg = context.args[0].strip()
        if arg.isdigit(): target_id = arg
        else: target_username = arg if arg.startswith('@') else f"@{arg}"

    # Kalau nggak ada target yang valid
    if not target_id and not target_username:
        return await update.message.reply_text("❌ Gunakan format <code>/check &lt;id/username&gt;</code> atau <i>reply</i> pesan dengan <code>/check</code>", parse_mode="HTML")

    # Cegah kalau yang dilacak adalah bot atau channel kita sendiri
    bot_me = await context.bot.get_me()
    if target_id == str(bot_me.id) or target_username == f"@{bot_me.username}":
        return await update.message.reply_text("❌ Tidak bisa mengecek bot atau channel.")

    # ---> PESAN LOADING DIKIRIM INSTAN DI SINI <---
    loading_msg = await update.message.reply_text("⏳ Mengumpulkan data target...", parse_mode="HTML")

    try:
        if target_id and not target_username:
            entity = await userbot.get_entity(int(target_id))
            if entity.username: target_username = f"@{entity.username}"
        elif target_username and not target_id:
            entity = await userbot.get_entity(target_username)
            target_id = str(entity.id)
    except Exception: pass

    targets_to_search, display_targets = [], []
    if target_id:
        targets_to_search.append(target_id)
        display_targets.append(f"<code>{target_id}</code>")
    if target_username:
        targets_to_search.append(target_username)
        display_targets.append(f"<code>{target_username}</code>")

    target_display = " & ".join(display_targets)

    # Update loading text setelah dapet data ID/Usernamenya
    await loading_msg.edit_text(f"⏳ Melacak rekam jejak {target_display} di database...", parse_mode="HTML")

    # FIX: Typo bantaipenip diperbaiki ke bantaipenipu
    channels = ["bantaipenipu", "rekampenipu", "spillhnr", "jejak_penipu"]
    found_posts = await search_with_userbot(targets_to_search, channels)

    if found_posts:
        teks_hasil = f"⚠️ <b>PERHATIAN!</b> Rekam jejak {target_display} <b>DITEMUKAN</b> di database.\n\nKemungkinan yang bersangkutan adalah pelaku/korban penipuan:\n"
        for link in found_posts: teks_hasil += f"𔐼 {link}\n"
        await loading_msg.edit_text(teks_hasil, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    else:
        keyboard = [[InlineKeyboardButton(f"🔍 Cek @{ch}", url=f"https://t.me/{ch}")] for ch in channels]
        reply_text = f"✅ {target_display} <b>belum ditemukan</b> di database otomatis kami.\n\n⚠️ Silakan buka dan cek ulang secara manual:"
        await loading_msg.edit_text(reply_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
        
# ==========================================
# MENFESS & NORMAL HANDLERS
# ==========================================
async def save_user(user_id, username):
    try: supabase.table("users").upsert({"user_id": user_id, "username": username}, on_conflict=["user_id"]).execute()
    except Exception: pass

async def start(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    user_id = update.effective_user.id
    if user_id in CACHE_BANNED_USERS: return await update.message.reply_text("❌ Akses kamu ke bot ini telah diblokir.")
    await save_user(user_id, update.effective_user.username)

    if await check_subscription(user_id, context):
        await update.message.reply_text(
            "Halo, selamat datang di *Bazarfess*! ☕️\n\n"
            "𔐼 *Bazarfess:* [@bazarfess](https://t.me/bazarfess)\n"
            "𔐼 *LPM Bazar:* [@lpmbazar](https://t.me/lpmbazar)\n"
            "𔐼 *Info Base:* [@rekapbazar](https://t.me/rekapbazar)\n\n"
            "Ketuk /menu untuk menampilkan navigasi", parse_mode="Markdown"
        )
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE
    if update.effective_chat.type != "private": return
    if not bot_active: return await update.message.reply_text("Bot sedang dipause oleh admin.")
    user_id = update.effective_user.id
    if user_id in CACHE_BANNED_USERS: return await update.message.reply_text("❌ Pesan ditolak. Akses kamu ke bot ini telah diblokir.")

    if update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        match = re.search(r"#ID:(\d+)", replied_text)
        if match:
            try:
                comment_msg_id = int(match.group(1))
                if update.message.text:
                    await context.bot.send_message(chat_id=GROUP_ID_DISKUSI, text=f"🗣️ *Balasan Sender:*\n\n{update.message.text}", reply_to_message_id=comment_msg_id, parse_mode="Markdown")
                else:
                    await context.bot.copy_message(chat_id=GROUP_ID_DISKUSI, from_chat_id=user_id, message_id=update.message.message_id, reply_to_message_id=comment_msg_id, caption=f"🗣️ *Balasan Sender:*\n\n{update.message.caption or ''}", parse_mode="Markdown")
                await update.message.reply_text("✅ Balasan anonim berhasil dikirim ke pengomentar!")
                return
            except Exception as e:
                logger.error(f"Gagal memproses balasan anonim (stateless): {e}")
                return await update.message.reply_text("❌ Gagal mengirim balasan anonim, mungkin komentar aslinya sudah dihapus.")

    username = update.effective_user.username
    first_name = update.effective_user.first_name
    display_name = f"@{username}" if username else first_name

    if not await check_subscription(user_id, context):
        keyboard = [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        return await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

    text_content = (update.message.text or update.message.caption or "").strip()
    text_content_lower = text_content.lower()
    is_direct_forward = any(ht.lower() in text_content_lower for ht in CACHE_HASHTAGS)

    text_without_hashtag = text_content
    for ht in CACHE_HASHTAGS:
        # Tambahkan #? di regex agar bot otomatis menghapus awalan # (jika ada) saat memfilter
        text_without_hashtag = re.sub(r'#?' + re.escape(ht), "", text_without_hashtag, flags=re.IGNORECASE)

    # Tambahkan .replace("#", "") sebagai proteksi ganda jika pengirim mengetik spasi setelah hashtag
    if is_direct_forward and not text_without_hashtag.replace("#", "").strip() and not (update.message.photo or update.message.video or update.message.document or update.message.audio or update.message.voice or update.message.sticker):
        return await update.message.reply_text("⚠️ Harap isi pesan terlebih dahulu sebelum mengirim!")

    if is_direct_forward:


        if MENFESS_MODE == "auto":
            for bw in CACHE_BAD_WORDS:
                if re.search(rf'\b{re.escape(bw)}\b', text_content_lower):
                    return await update.message.reply_text("❌ Menfess ditolak karena mengandung kata-kata yang dilarang oleh base.")
            if len(text_content) > 280:
                return await update.message.reply_text(f"❌ Menfess terlalu panjang! Maksimal 280 karakter. (Karakter kamu: {len(text_content)}).")

            ada_mention = False
            entities = update.message.entities or update.message.caption_entities or []
            for ent in entities:
                if ent.type == "mention": ada_mention = True; break

            if ada_mention or re.search(r'(?:^|\s)@/?\w+', text_content):
                return await update.message.reply_text("❌ Menfess dilarang menyertakan mention atau username!")

            try:
                if update.message.text:
                    message_sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=update.message.text, entities=update.message.entities, link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True))
                else:
                    message_sent = await context.bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=user_id, message_id=update.message.message_id)

                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
                await update.message.reply_text("Pesan kamu telah dikirim ke channel! 🪶\n\nJangan lupa kepoin channel base ya!", reply_markup=InlineKeyboardMarkup(keyboard))
                try: supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute()
                except Exception: pass

                log_msg = f"📌 Log Menfess (AUTO):\n🕰️ Waktu: {update.message.date}\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Lihat Pesan", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]))
            except Exception: await update.message.reply_text("❌ Terjadi kesalahan saat mengirim menfess.")
        else:
            try:
                fw_msg = await context.bot.copy_message(chat_id=ADMIN_GROUP_ID, from_chat_id=user_id, message_id=update.message.message_id)
                keyboard = [
                    [InlineKeyboardButton("✅ Acc (CS ON)", callback_data=f"mf|A_ON|{user_id}|{update.message.message_id}"), InlineKeyboardButton("🔕 Acc (CS OFF)", callback_data=f"mf|A_OFF|{user_id}|{update.message.message_id}")],
                    [InlineKeyboardButton("❌ Tolak", callback_data=f"mf|R|{user_id}|{update.message.message_id}")]
                ]
                review_text = f"🚨 *REVIEW MENFESS BARU*\n👤 Pengirim: {display_name}\n🆔 ID: `{user_id}`"
                await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=review_text, reply_to_message_id=fw_msg.message_id, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                await update.message.reply_text("⏳ Menfess kamu sedang masuk ke antrean admin untuk direview. Mohon tunggu ya!")
            except Exception: await update.message.reply_text("❌ Gagal mengirim menfess ke admin review.")
    else:
        try:
            if update.message.text:
                text_msg = f"📩 Pesan dari: {first_name}\n👤 Username: {display_name}\n🆔 ID: {user_id}\n\n💬 Pesan:\n{update.message.text}"
                await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_msg)
            else:
                cap = f"📩 Pesan dari: {first_name}\n👤 Username: {display_name}\n🆔 ID: {user_id}\n\n💬 Pesan:\n{update.message.caption or ''}"
                await context.bot.copy_message(chat_id=ADMIN_GROUP_ID, from_chat_id=user_id, message_id=update.message.message_id, caption=cap)
            await update.message.reply_text("Pesan kamu telah dikirim ke admin. Jika ini pesan seharusnya untuk menfess, silahkan kirim kembali menggunakan #bazar.")
        except Exception: await update.message.reply_text("Tipe pesan tidak didukung atau terjadi kesalahan.")

async def handle_callback_review(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    if data.startswith("mf|"):
        await query.answer()
        parts = data.split("|")
        action, user_id, msg_id = parts[1], int(parts[2]), int(parts[3])

        if action in ["A_ON", "A_OFF"]:
            comsect_on = (action == "A_ON")
            status_text = "DISETUJUI & COMSECT ON" if comsect_on else "DISETUJUI & COMSECT OFF"
            try:
                original_msg = query.message.reply_to_message
                if original_msg and original_msg.text:
                    sent_msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=original_msg.text, entities=original_msg.entities, link_preview_options=LinkPreviewOptions(is_disabled=False, prefer_large_media=True))
                else:
                    sent_msg = await context.bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=ADMIN_GROUP_ID, message_id=original_msg.message_id)

                if not comsect_on: CACHE_COMSECT_OFF.add(sent_msg.message_id)
                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")
                try: supabase.table("menfess_map").insert({"post_id": sent_msg.message_id, "sender_user_id": user_id}).execute()
                except Exception: pass

                await query.edit_message_text(f"{query.message.text}\n\n✅ *STATUS: {status_text}*", parse_mode="Markdown")
                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{sent_msg.message_id}")]]
                await context.bot.send_message(chat_id=user_id, text=f"✅ Yay! Menfess kamu telah disetujui admin! ({status_text})", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception: await query.edit_message_text(f"{query.message.text}\n\n❌ *GAGAL DIPUBLISH:* Pesan mungkin sudah dihapus.", parse_mode="Markdown")
        elif action == "R":
            await query.edit_message_text(f"{query.message.text}\n\n❌ *STATUS: DITOLAK*", parse_mode="Markdown")
            warning_text = (
                "⚠️ *Menfess Ditolak*\n\n"
                "Maaf, menfess kamu ditolak oleh admin karena belum sesuai dengan rules base. "
                "Silakan perbaiki format/isi menfess kamu dan kirim ulang ya!"
            )
            await context.bot.send_message(chat_id=user_id, text=warning_text, parse_mode="Markdown")

async def handle_admin_reply(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID or not update.message.reply_to_message: return
    match = re.search(r"ID(?:\s*Pengguna)?:?\s*(\d+)", update.message.reply_to_message.text or update.message.reply_to_message.caption or "")
    if not match: return
    user_id = int(match.group(1))
    reply_text = update.message.text or update.message.caption

    if reply_text and reply_text.startswith("/"):
        try:
            response = supabase.table("commands").select("content").eq("name", reply_text.split()[0]).execute()
            if hasattr(response, 'data') and response.data:
                await context.bot.send_message(chat_id=user_id, text=response.data[0]["content"], parse_mode="Markdown")
                notif = await update.message.reply_text(f"✅ Command dikirim ke user {user_id}")
                await asyncio.sleep(5)
                try: await notif.delete()
                except: pass
        except Exception: pass
        return

    try:
        await context.bot.copy_message(chat_id=user_id, from_chat_id=ADMIN_GROUP_ID, message_id=update.message.message_id)
        notif = await update.message.reply_text("✅ Balasan telah dikirim ke user.")
        await asyncio.sleep(5)
        try: await notif.delete()
        except: pass
    except Exception: await update.message.reply_text("❌ Gagal mengirim balasan.")

async def handle_discussion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    if msg.is_automatic_forward and msg.forward_origin and msg.forward_origin.type == "channel":
        post_id = msg.forward_origin.message_id
        if post_id in CACHE_COMSECT_OFF:
            try:
                await msg.delete()
                CACHE_COMSECT_OFF.discard(post_id)
                return
            except Exception: pass

        origin_chat = msg.forward_origin.chat
        if origin_chat.username and ("@" + origin_chat.username.lower() == CHANNEL_ID.lower()):
            try: supabase.table("menfess_map").update({"discussion_message_id": msg.message_id}).eq("post_id", post_id).execute()
            except Exception: pass

            warning_rekber = (
                "⚠️ *HIMBAUAN KEAMANAN TRANSAKSI JUAL/BELI*\n\n"
                "Halo! Untuk segala bentuk transaksi jual/beli "
                "kami sangat mewajibkan melalui **REKBER** terpercaya "
                "untuk mencegah penipuan. Jangan mudah tergiur harga murah, "
                "tetap waspada dan hati-hati bazzer!"
            )
            try: await context.bot.send_message(chat_id=msg.chat_id, text=warning_rekber, reply_to_message_id=msg.message_id, parse_mode="Markdown")
            except Exception: pass
        return

    if msg.reply_to_message and msg.from_user.id != context.bot.id:
        try:
            replied_msg_id = msg.reply_to_message.message_id
            response = supabase.table("menfess_map").select("sender_user_id, post_id").eq("discussion_message_id", replied_msg_id).execute()
            if hasattr(response, 'data') and response.data:
                sender_user_id, post_id = response.data[0]["sender_user_id"], response.data[0]["post_id"]
                commenter = f"{msg.from_user.first_name} (@{msg.from_user.username})" if msg.from_user.username else msg.from_user.first_name
                link = f"https://t.me/{CHANNEL_ID.lstrip('@')}/{post_id}?comment={msg.message_id}"
                notif_text = (
                    f"📬 {commenter} berkomentar di menfess kamu!\n\n"
                    f"*(balas/reply pesan ini jika kamu ingin membalas komentarnya secara anonim)*\n\n"
                    f"`#ID:{msg.message_id}`"
                )
                await context.bot.send_message(chat_id=sender_user_id, text=notif_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Lihat Balasan", url=link)]]), parse_mode="Markdown")
        except Exception: pass

async def open_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = True
        await update.message.reply_text("✅ Bot telah diaktifkan kembali.")

async def close_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = False
        await update.message.reply_text("⏸️ Bot telah dipause.")

async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")

async def get_all_user_ids():
    try:
        response = supabase.table("users").select("user_id").execute()
        return [row["user_id"] for row in response.data] if hasattr(response, "data") and response.data else []
    except Exception: return []

async def menu(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    menu_text = "𔐼 *Bazarfess:* [@bazarfess](https://t.me/bazarfess)\n𔐼 *LPM Bazar:* [@lpmbazar](https://t.me/lpmbazar)\n𔐼 *Info Base:* [@rekapbazar](https://t.me/rekapbazar)\n\n"
    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📜 Info Bazar", url="https://t.me/rekapbazar")]]))

async def broadcast_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcastfw <link>")
    link = context.args[0]
    match = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", link)
    if not match: return await update.message.reply_text("❌ Link tidak valid! Pastikan formatnya t.me/username_channel/angka")
    channel_username, message_id = match.groups()
    if channel_username == "c": return await update.message.reply_text("❌ Tidak bisa forward menggunakan link dari channel private!")

    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database untuk dibroadcast.")

    sc, fc = 0, 0
    status_msg = await update.message.reply_text(f"⏳ *Memulai proses broadcast forward ke {total_users} user...*\nMohon tunggu ya!", parse_mode="Markdown")
    for i, user_id in enumerate(user_list, 1):
        try:
            await context.bot.forward_message(chat_id=user_id, from_chat_id=f"@{channel_username}", message_id=int(message_id))
            sc += 1
        except Exception: fc += 1
        if i % 20 == 0:
            try: await status_msg.edit_text(f"⏳ *Sedang memproses broadcast... ({i}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
            except Exception: pass
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ *Broadcast Forward Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcast <teks>")
    message_text = " ".join(context.args)
    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database untuk dibroadcast.")

    sc, fc = 0, 0
    status_msg = await update.message.reply_text(f"⏳ *Memulai proses broadcast ke {total_users} user...*\nMohon tunggu ya!", parse_mode="Markdown")
    for i, user_id in enumerate(user_list, 1):
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            sc += 1
        except Exception: fc += 1
        if i % 20 == 0:
            try: await status_msg.edit_text(f"⏳ *Sedang memproses broadcast... ({i}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
            except Exception: pass
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ *Broadcast Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

async def add_command(update: Update, context: CallbackContext) -> None:
    if update.message.reply_to_message:
        command_name = context.args[0] if context.args else None
        command_content = update.message.reply_to_message.text
    else:
        if len(context.args) < 2: return await update.message.reply_text("Format: /addcommand <nama> <isi>")
        command_name, command_content = context.args[0], " ".join(context.args[1:])
    command_name = command_name if command_name.startswith("/") else "/" + command_name
    try:
        supabase.table("commands").upsert({"name": command_name, "content": command_content}).execute()
        await update.message.reply_text(f"✅ `{command_name}` disimpan!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def delete_command(update: Update, context: CallbackContext) -> None:
    if not context.args: return await update.message.reply_text("Format: /deletecommand <nama>")
    command_name = context.args[0] if context.args[0].startswith("/") else "/" + context.args[0]
    try:
        supabase.table("commands").delete().eq("name", command_name).execute()
        await update.message.reply_text(f"✅ `{command_name}` dihapus!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def settings(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    channels_text = "\n".join([f"𔐼 {c}" for c in required_channels]) if required_channels else "–"
    hashtags_text = "\n".join([f"𔐼 `{h}`" for h in CACHE_HASHTAGS]) if CACHE_HASHTAGS else "–"
    global MENFESS_MODE
    try:
        response = supabase.table("commands").select("name, content").execute()
        commands_text = "\n\n".join([f"*{c['name']}*\n{c['content']}" for c in response.data]) if hasattr(response, 'data') and response.data else "–"
    except Exception: commands_text = "– Error –"

    await update.message.reply_text(
        f"⚙️ *Settings*\n\n"
        f"🔄 *Mode Menfess:* `{MENFESS_MODE.upper()}`\n\n"
        f"📌 *Channels:*\n{channels_text}\n\n"
        f"🏷️ *Hashtags:*\n{hashtags_text}\n\n"
        f"💻 *Commands:*\n{commands_text}",
        parse_mode="Markdown"
    )

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# ==========================================
# FUNGSI MAIN (PENTING: SEMUA HANDLER DIGABUNG)
# ==========================================
def main():
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # Commands admin Base
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('unblock', unblock_user))
    application.add_handler(CommandHandler('auto', set_mode_auto))
    application.add_handler(CommandHandler('manual', set_mode_manual))
    application.add_handler(CommandHandler("addbadwords", add_badwords))
    application.add_handler(CommandHandler("removebadwords", remove_badwords))
    application.add_handler(CommandHandler("listbadwords", list_badwords))

    # Fitur Utama
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', menu))
    application.add_handler(CommandHandler('open', open_bot))
    application.add_handler(CommandHandler('close', close_bot))
    application.add_handler(CommandHandler('grupid', get_group_id))
    application.add_handler(CommandHandler('setrequired', set_required_channels))
    application.add_handler(CommandHandler("addhashtag", add_hashtag))
    application.add_handler(CommandHandler("removehashtag", remove_hashtag))
    application.add_handler(CommandHandler("enablehashtag", enable_hashtag))
    application.add_handler(CommandHandler("disablehashtag", disable_hashtag))
    application.add_handler(CommandHandler('broadcastfw', broadcast_forward))
    application.add_handler(CommandHandler('broadcast', broadcast))
    application.add_handler(CommandHandler("addcommand", add_command))
    application.add_handler(CommandHandler("deletecommand", delete_command))
    application.add_handler(CommandHandler("settings", settings))

  # Ini dia handler fitur barunya (Check Penipu)
    application.add_handler(CommandHandler("check", check_penipu))

    # Message & Callback Handlers
    application.add_handler(CallbackQueryHandler(handle_callback_review))
    application.add_handler(MessageHandler(filters.ALL & filters.Chat(ADMIN_GROUP_ID), handle_admin_reply))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(MessageHandler(filters.Chat(GROUP_ID_DISKUSI), handle_discussion))
    application.add_handler(MessageHandler(filters.ALL & filters.ChatType.PRIVATE, handle_pesan))

    logger.info("✅ Membangun bot selesai. Menjalankan polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == '__main__':
    main()
