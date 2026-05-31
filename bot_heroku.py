import json
import logging
import re
import html
import markdown
import os
import asyncio


from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, CallbackContext
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, LinkPreviewOptions
from supabase import create_client
import os
from telethon.sessions import StringSession

# Import Telethon untuk Userbot
from telethon import TelegramClient

# Tarik data dari Config Vars
try:
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    CHANNEL_ID = os.getenv('CHANNEL_ID')
    GROUP_ID_DISKUSI = int(os.getenv('GROUP_ID_DISKUSI') or 0)
    ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID') or 0)
    LOG_GROUP_ID = int(os.getenv('LOG_GROUP_ID') or 0)
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')

    # === KREDENSIAL USERBOT TELETHON ===
    USERBOT_API_ID = int(os.getenv('USERBOT_API_ID') or 0)
    USERBOT_API_HASH = os.getenv('USERBOT_API_HASH')
    USERBOT_PHONE = os.getenv('USERBOT_PHONE')
    
    # Penarikan string session
    USERBOT_SESSION = os.getenv('USERBOT_SESSION_STRING')
except Exception as e:
    print(f"⚠️ Error mengambil Secrets dari Heroku: {e}")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

bot_active = True
MENFESS_MODE = "auto"
SELLER_VERIFIED_TITLE = os.getenv("SELLER_VERIFIED_TITLE", "verified 🫆")
KEYBOARD_STATE_VERIFY_SELLER = "WAITING_SELLER_VERIFICATION_FORM"
KEYBOARD_STATE_CHECK_PENIPU = "WAITING_CHECK_PENIPU_TARGET"
KEYBOARD_STATE_RADAR_ADD = "WAITING_RADAR_ADD"
KEYBOARD_STATE_RADAR_REMOVE = "WAITING_RADAR_REMOVE"

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    logger.error(f"Gagal koneksi ke Supabase: {e}")

# Inisialisasi Client Telethon (Userbot)
userbot = TelegramClient(StringSession(USERBOT_SESSION), USERBOT_API_ID, USERBOT_API_HASH)

CACHE_HASHTAGS = []
required_channels = []
CACHE_BANNED_USERS = []
CACHE_COMSECT_OFF = set()
CACHE_BAD_WORDS = set()
CACHE_RADAR = {}

# ==========================================
# NON-BLOCKING DATABASE HELPER
# ==========================================
async def db(fn):
    """Jalankan query Supabase sinkron di thread agar event loop bot tidak stuck saat ramai."""
    return await asyncio.to_thread(fn)

# ==========================================
# CACHE & STARTUP FUNCTIONS
# ==========================================
async def update_settings_cache():
    global MENFESS_MODE, bot_active
    try:
        response = await db(lambda: supabase.table("bot_settings").select("key, value").execute())
        if hasattr(response, 'data') and response.data:
            settings = {row["key"]: row["value"] for row in response.data}
            MENFESS_MODE = settings.get("menfess_mode", "auto")
            bot_active = str(settings.get("bot_active", "true")).lower() != "false"
        else:
            await db(lambda: supabase.table("bot_settings").insert({"key": "menfess_mode", "value": "auto"}).execute())
            await db(lambda: supabase.table("bot_settings").insert({"key": "bot_active", "value": "true"}).execute())
            MENFESS_MODE = "auto"
            bot_active = True
    except Exception as e: logger.error(f"Gagal memuat setting bot: {e}")

async def update_hashtags_cache():
    global CACHE_HASHTAGS
    try:
        response = await db(lambda: supabase.table("triggered_hashtags").select("hashtag").eq("active", True).execute())
        CACHE_HASHTAGS = [row["hashtag"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e: logger.error(f"Gagal memuat cache hashtag: {e}")

async def update_badwords_cache():
    global CACHE_BAD_WORDS
    try:
        response = await db(lambda: supabase.table("bad_words").select("word").execute())
        CACHE_BAD_WORDS = {row["word"].lower() for row in response.data} if hasattr(response, 'data') and response.data else set()
    except Exception as e: logger.error(f"Gagal memuat cache bad words: {e}")

async def update_radar_cache():
    global CACHE_RADAR
    try:
        response = await db(lambda: supabase.table("user_radars").select("user_id, keyword").execute())
        new_cache = {}
        if hasattr(response, 'data') and response.data:
            for row in response.data:
                kw = row['keyword'].lower()
                if kw not in new_cache: 
                    new_cache[kw] = set()
                new_cache[kw].add(row['user_id'])
        CACHE_RADAR = new_cache
    except Exception as e: 
        logger.error(f"Gagal memuat cache radar: {e}")

async def update_required_channels_cache():
    global required_channels
    try:
        response = await db(lambda: supabase.table('required_channels').select("channel_username").execute())
        required_channels = [row["channel_username"] for row in response.data] if hasattr(response, 'data') and response.data else []
    except Exception as e: logger.error(f"Gagal memuat required channels: {e}")

async def update_banned_users_cache():
    global CACHE_BANNED_USERS
    try:
        response = await db(lambda: supabase.table('banned_users').select("user_id").execute())
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
        await userbot.start(phone=USERBOT_PHONE)
        logger.info("✅ Userbot (Akun Asli) siap dan terhubung!")
    except Exception as e:
        logger.error(f"⚠️ Gagal get_me atau start userbot saat startup: {e}")

async def save_required_channels(channels):
    try:
        await db(lambda: supabase.table('required_channels').delete().neq("channel_username", "").execute())
        for channel in channels:
            await db(lambda: supabase.table('required_channels').insert({"channel_username": channel}).execute())
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
                await db(lambda: supabase.table("bad_words").upsert({"word": w}).execute())
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
                await db(lambda: supabase.table("bad_words").delete().eq("word", w).execute())
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
        await db(lambda: supabase.table("banned_users").upsert({"user_id": target_id}).execute())
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil diblokir dari bot.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("❌ Gagal memblokir user. Pastikan format ID benar.")

async def unblock_user(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Format: /unblock <user_id>")
    try:
        target_id = int(context.args[0])
        await db(lambda: supabase.table("banned_users").delete().eq("user_id", target_id).execute())
        await update_banned_users_cache()
        await update.message.reply_text(f"✅ User `{target_id}` berhasil di-unblock.", parse_mode="Markdown")
    except Exception: await update.message.reply_text("❌ Gagal unblock user.")

async def set_mode_auto(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "auto"
    try: await db(lambda: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "auto"}).execute())
    except Exception: pass
    await update.message.reply_text("✅ Mode menfess diubah ke *AUTO*. Menfess akan langsung terkirim ke channel.", parse_mode="Markdown")

async def set_mode_manual(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    global MENFESS_MODE
    MENFESS_MODE = "manual"
    try: await db(lambda: supabase.table("bot_settings").upsert({"key": "menfess_mode", "value": "manual"}).execute())
    except Exception: pass
    await update.message.reply_text("⏸️ Mode menfess diubah ke *MANUAL*. Menfess akan masuk ke grup admin untuk direview terlebih dahulu.", parse_mode="Markdown")

async def add_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /addhashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").upsert({"hashtag": hashtag}).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` berhasil ditambahkan!", parse_mode="Markdown")

async def remove_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /removehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").delete().eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"❌ Hashtag `{hashtag}` berhasil dihapus!", parse_mode="Markdown")

async def enable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /enablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").update({"active": True}).eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"✅ Hashtag `{hashtag}` diaktifkan!", parse_mode="Markdown")

async def disable_hashtag(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /disablehashtag <hashtag>")
    hashtag = context.args[0].strip()
    await db(lambda: supabase.table("triggered_hashtags").update({"active": False}).eq("hashtag", hashtag).execute())
    await update_hashtags_cache()
    await update.message.reply_text(f"⚠️ Hashtag `{hashtag}` dinonaktifkan!", parse_mode="Markdown")

async def set_required_channels(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    if not context.args: return await update.message.reply_text("Gunakan format: /setrequired @channel1 @channel2")
    global required_channels
    required_channels = context.args
    await save_required_channels(required_channels)
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
            except Exception: pass
    return found_links

async def process_check_penipu(update: Update, context: CallbackContext, raw_target: str = None):
    target_id = None
    target_username = None
    phone_variations = []
    arg_extracted = None

    # 1. Jika dipanggil dari tombol keyboard, target dibaca dari teks yang dikirim user.
    if raw_target:
        arg_extracted = raw_target.strip()
    
    # 2. PRIORITAS COMMAND: Cek apakah command ini me-reply sebuah pesan.
    elif update.message.reply_to_message:
        replied_msg = update.message.reply_to_message

        # Ekstrak ID dari pesan laporan/log jika dilakukan dari Grup Admin
        if update.effective_chat.id == ADMIN_GROUP_ID:
            replied_text = replied_msg.text or replied_msg.caption or ""
            match_id = re.search(r"ID:?\s*`?(\d+)`?", replied_text, re.IGNORECASE)
            match_user = re.search(r"Username:?\s*@?([a-zA-Z0-9_]+)", replied_text, re.IGNORECASE)

            if match_id: target_id = match_id.group(1)
            if match_user and match_user.group(1).lower() != 'none': target_username = f"@{match_user.group(1)}"

        # Jika bukan di grup admin (di comsect), atau gagal ekstrak dari format teks admin
        if not target_id and not target_username:
            target_id = str(replied_msg.from_user.id)
            if replied_msg.from_user.username: target_username = f"@{replied_msg.from_user.username}"

    # 3. Jika BUKAN reply pesan, baru baca teks setelah command (misal: /check @username atau 08xxx)
    elif context.args:
        arg_extracted = context.args[0].strip()

    # Eksekusi Parsing (Membedakan Username, ID, atau No HP)
    if arg_extracted:
        arg_clean = arg_extracted.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").strip("/")
        
        # Deteksi Nomer HP Indonesia (Awalan +62, 62, atau 08)
        num_only = re.sub(r'[^0-9+]', '', arg_clean)
        if re.match(r'^(?:\+62|62|0)8[0-9]{6,13}$', num_only):
            # Ambil core angkanya saja setelah prefix
            if num_only.startswith('+62'): core = num_only[3:]
            elif num_only.startswith('62'): core = num_only[2:]
            elif num_only.startswith('0'): core = num_only[1:]
            
            # Buat array variasi format nomor HP
            phone_variations = [f"0{core}", f"+62{core}", f"62{core}"]
        elif arg_clean.isdigit(): 
            target_id = arg_clean
        else: 
            target_username = arg_clean if arg_clean.startswith('@') else f"@{arg_clean}"

    # Kalau nggak ada target yang valid
    if not target_id and not target_username and not phone_variations:
        return await update.message.reply_text("❌ Gunakan format <code>/check &lt;id/username/nohp&gt;</code> atau <i>reply</i> pesan dengan <code>/check</code>", parse_mode="HTML")

    # Cegah kalau yang dilacak adalah bot atau channel kita sendiri
    bot_me = await context.bot.get_me()
    if target_id == str(bot_me.id) or target_username == f"@{bot_me.username}":
        return await update.message.reply_text("❌ Tidak bisa mengecek bot atau channel.")

    loading_msg = await update.message.reply_text("⏳ Mengumpulkan data target...", parse_mode="HTML")

    # Resolve Username/ID ke entitas Telegram via Userbot (Lewati jika target berupa No HP)
    try:
        if target_id and not target_username:
            entity = await userbot.get_entity(int(target_id))
            if entity.username: target_username = f"@{entity.username}"
        elif target_username and not target_id:
            entity = await userbot.get_entity(target_username)
            target_id = str(entity.id)
    except Exception:
        pass

    # Gabungkan target yang akan di-search ke channel
    targets_to_search, display_targets = [], []
    
    if target_id:
        targets_to_search.append(target_id)
        display_targets.append(f"<code>{target_id}</code>")
    if target_username:
        targets_to_search.append(target_username)
        display_targets.append(f"<code>{target_username}</code>")
    if phone_variations:
        targets_to_search.extend(phone_variations)
        display_targets.extend([f"<code>{p}</code>" for p in phone_variations])

    target_display = " & ".join(display_targets)
    await loading_msg.edit_text(f"⏳ Melacak rekam jejak {target_display} di database...", parse_mode="HTML")

    # Mulai pencarian menggunakan Telethon
    channels = ["bantaipenip", "rekampenipu", "spillhnr", "jejak_penipu"]
    found_posts = await search_with_userbot(targets_to_search, channels)

    # Tambahkan Note khusus jika pencarian melibatkan nomor HP
    note_tambahan = ""
    if phone_variations:
        note_tambahan = "\n\n📝 <b>Note:</b> untuk pengecheckan via nomer wajib waspada, kadang penipu berganti ganti payment."

    if found_posts:
        teks_hasil = f"⚠️ <b>PERHATIAN!</b> Rekam jejak {target_display} <b>DITEMUKAN</b> di database.\n\nKemungkinan yang bersangkutan adalah pelaku/korban penipuan:\n"
        for link in found_posts: teks_hasil += f"𔐼 {link}\n"
        teks_hasil += note_tambahan
        await loading_msg.edit_text(teks_hasil, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))
    else:
        keyboard = [[InlineKeyboardButton(f"🔍 Cek @{ch}", url=f"https://t.me/{ch}")] for ch in channels]
        reply_text = f"✅ {target_display} <b>belum ditemukan</b> di database otomatis kami.\n\n⚠️ Silakan buka dan cek ulang secara manual:"
        reply_text += note_tambahan
        await loading_msg.edit_text(reply_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True))

async def check_penipu(update: Update, context: CallbackContext):
    await process_check_penipu(update, context)

# ==========================================
# MENFESS & NORMAL HANDLERS
# ==========================================
async def save_user(user_id, username):
    try: await db(lambda: supabase.table("users").upsert({"user_id": user_id, "username": username}, on_conflict="user_id").execute())
    except Exception: pass


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("✅ Verifikasi Seller"), KeyboardButton("🔍 Check Penipu")],
        [KeyboardButton("📡 Radar Incaran"), KeyboardButton("👤 Profile")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_radar_keyboard():
    keyboard = [
        [KeyboardButton("➕ Tambah Radar"), KeyboardButton("➖ Hapus Radar")],
        [KeyboardButton("📋 List Radar"), KeyboardButton("❌ Cancel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True, is_persistent=True)


def seller_form_template():
    return (
        "🧾 FORM VERIFIKASI SELLER\n\n"
        "Silakan copy format di bawah ini:\n\n"
        "```\n"
        "Username: @username_kamu\n"
        "Channel BA: @channel\n"
        "Testimoni: link testimoni\n"
        "Honest Review: link review\n"
        "```\n\n"
        "Tekan ❌ Cancel untuk membatalkan."
    )


def parse_seller_form(text: str):
    aliases = {
        "username": "username",
        "user": "username",
        "channel": "channel_ba",
        "channel ba": "channel_ba",
        "ch ba": "channel_ba",
        "ba": "channel_ba",
        "testimoni": "testimoni",
        "testimony": "testimoni",
        "testi": "testimoni",
        "honest review": "honest_review",
        "honest": "honest_review",
        "review": "honest_review",
    }
    labels = {
        "username": "Username",
        "channel_ba": "Channel BA",
        "testimoni": "Testimoni",
        "honest_review": "Honest Review",
    }
    data = {key: "" for key in labels}
    current_key = None

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if current_key and data[current_key]:
                data[current_key] += "\n"
            continue

        match = re.match(r"^(username|user|channel\s*ba|ch\s*ba|channel|ba|testimoni|testimony|testi|honest\s*review|honest|review)\s*:\s*(.*)$", line, re.IGNORECASE)
        if match:
            raw_key = re.sub(r"\s+", " ", match.group(1).lower()).strip()
            current_key = aliases.get(raw_key)
            if current_key:
                data[current_key] = match.group(2).strip()
            continue

        if current_key:
            if data[current_key] and not data[current_key].endswith("\n"):
                data[current_key] += "\n"
            data[current_key] += line

    data = {key: value.strip() for key, value in data.items()}
    missing = [labels[key] for key, value in data.items() if not value]
    return data, missing


async def set_user_seller_status(user_id: int, status: str):
    """Update kolom seller_status di tabel users"""
    try:
        await db(lambda: supabase.table("users").update({"seller_status": status}).eq("user_id", user_id).execute())
    except Exception as e:
        logger.warning(f"Gagal update status seller di tabel users: {e}")

async def get_seller_status(user_id: int):
    """Ambil status dari tabel users"""
    try:
        response = await db(lambda: supabase.table("users").select("seller_status").eq("user_id", user_id).execute())
        if hasattr(response, "data") and response.data:
            status = (response.data[0].get("seller_status") or "non-seller").lower()
            if status == "verified":
                return f"Terverifikasi ({SELLER_VERIFIED_TITLE})"
            if status == "pending":
                return "Menunggu review admin"
            if status == "rejected":
                return "Ditolak"
    except Exception:
        pass
    return "Non-Seller"


async def cek_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    await save_user(user.id, user.username)
    seller_status = await get_seller_status(user.id)
    await update.message.reply_text(
        f"👤 <b>PROFILE KAMU</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🏷️ Status Seller: <b>{html.escape(seller_status)}</b>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard()
    )


async def start_seller_verification(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private":
        return
    context.user_data["keyboard_state"] = KEYBOARD_STATE_VERIFY_SELLER
    await update.message.reply_text(seller_form_template(), reply_markup=get_cancel_keyboard(), parse_mode="Markdown")


async def submit_seller_verification(update: Update, context: CallbackContext):
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    form_data, missing = parse_seller_form(text)

    if missing:
        await update.message.reply_text(
            "❌ Form belum lengkap. Bagian yang kosong: " + ", ".join(missing) + "\n\n" + seller_form_template(),
            reply_markup=get_cancel_keyboard(), parse_mode="Markdown"
        )
        context.user_data["keyboard_state"] = KEYBOARD_STATE_VERIFY_SELLER
        return

    display_name = f"@{user.username}" if user.username else user.first_name
    
    # Hanya ubah statusnya di tabel users menjadi pending, tanpa save data form
    await set_user_seller_status(user.id, "pending")

    admin_text = (
        "🧾 <b>PENGAJUAN VERIFIKASI SELLER</b>\n\n"
        f"👤 Pengirim: {html.escape(display_name)}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🔗 Username Telegram: {html.escape('@' + user.username if user.username else '-')}\n\n"
        "📌 <b>Data Form</b>\n"
        f"<b>Username:</b> {html.escape(form_data['username'])}\n"
        f"<b>Channel BA:</b> {html.escape(form_data['channel_ba'])}\n"
        f"<b>Testimoni:</b> {html.escape(form_data['testimoni'])}\n"
        f"<b>Honest Review:</b> {html.escape(form_data['honest_review'])}"
    )
    keyboard = [[
        InlineKeyboardButton("✅ Setujui", callback_data=f"seller|A|{user.id}"),
        InlineKeyboardButton("❌ Tolak", callback_data=f"seller|R|{user.id}"),
    ]]

    try:
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=admin_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        await update.message.reply_text(
            "✅ Form verifikasi seller kamu sudah dikirim ke admin. Mohon tunggu hasil review ya.",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Gagal mengirim form verifikasi seller ke admin: {e}")
        await update.message.reply_text("❌ Gagal mengirim form ke admin. Silakan coba lagi nanti.", reply_markup=get_main_keyboard())

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
            "Ketuk /menu untuk menampilkan navigasi", parse_mode="Markdown", reply_markup=get_main_keyboard()
        )
    else:
        keyboard = [[InlineKeyboardButton("Join Channels", url=f"https://t.me/{c[1:]}")] for c in required_channels]
        await update.message.reply_text("Sebelum lanjut, silakan join channel berikut dulu ya!", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

async def trigger_radar_notifications(context: CallbackContext, text_content: str, post_url: str):
    text_lower = text_content.lower()
    notified_users = set()
    
    # Scan seluruh keyword yang ada di cache
    for keyword, users in CACHE_RADAR.items():
        # Menggunakan regex boundary \b agar tidak salah deteksi (contoh: keyword "ml" tidak trigger di kata "html")
        if re.search(rf'\b{re.escape(keyword)}\b', text_lower):
            for uid in users:
                notified_users.add(uid)
                
    # Kirim notifikasi ke user yang nyangkut
    for uid in notified_users:
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📡 *BINGO! Radar Incaranmu Berbunyi!*\n\nAda menfess baru yang cocok dengan keyword radarmu. Langsung sikat sebelum keduluan orang lain:\n{post_url}", 
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.05) # Jeda kecil menghindari flood limit Telegram
        except Exception:
            pass # Skip jika user memblokir bot

async def handle_pesan(update: Update, context: CallbackContext):
    global bot_active, MENFESS_MODE
    if not update.effective_user or not update.message: return
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

    if update.message.text == "❌ Cancel":
        context.user_data.pop("keyboard_state", None)
        return await update.message.reply_text("✅ Aksi dibatalkan. Kembali ke menu utama.", reply_markup=get_main_keyboard())

    if text_content == "📡 Radar Incaran":
        return await update.message.reply_text(
            "📡 *Smart Radar Kitheons*\n\n"
            "Pilih menu di bawah ini untuk mengatur radar incaranmu:", 
            parse_mode="Markdown",
            reply_markup=get_radar_keyboard()
        )
        
    if text_content == "📋 List Radar":
        response = await db(lambda: supabase.table("user_radars").select("keyword").eq("user_id", user_id).execute())
        keywords = [r['keyword'] for r in response.data] if hasattr(response, 'data') and response.data else []
        if not keywords: 
            return await update.message.reply_text("Kamu belum punya keyword radar incaran.", reply_markup=get_radar_keyboard())
        return await update.message.reply_text(f"📡 *Radar Incaranmu:*\n- " + "\n- ".join(keywords), parse_mode="Markdown", reply_markup=get_radar_keyboard())
        
    if text_content == "➕ Tambah Radar":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_RADAR_ADD
        return await update.message.reply_text(
            "Ketik *satu keyword* barang incaran yang ingin ditambahkan ke radar (Maks 10 keyword).\n\n"
            "Contoh: `netflix`",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        
    if text_content == "➖ Hapus Radar":
        context.user_data["keyboard_state"] = KEYBOARD_STATE_RADAR_REMOVE
        return await update.message.reply_text(
            "Ketik keyword radar yang ingin dihapus.\n\n"
            "(_Cek 📋 List Radar dulu jika kamu lupa ejaannya_)",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )

    keyboard_state = context.user_data.get("keyboard_state")

    if keyboard_state == KEYBOARD_STATE_RADAR_ADD:
        context.user_data.pop("keyboard_state", None)
        
        # Pisahkan input berdasarkan koma, ubah ke huruf kecil, dan hapus spasi berlebih
        raw_keywords = [k.strip().lower() for k in text_content.split(',') if k.strip()]
        
        if not raw_keywords:
            return await update.message.reply_text("❌ Keyword tidak boleh kosong.", reply_markup=get_radar_keyboard())

        # Hilangkan duplikat dari ketikan user (misal dia ngetik "netflix, netflix")
        new_keywords = []
        for k in raw_keywords:
            if k not in new_keywords:
                new_keywords.append(k)

        # Ambil keyword yang sudah ada di database
        response = await db(lambda: supabase.table("user_radars").select("keyword").eq("user_id", user_id).execute())
        current_kws = [r['keyword'].lower() for r in response.data] if hasattr(response, 'data') and response.data else []
        
        # Saring keyword yang sebenarnya benar-benar baru (belum ada di database)
        to_add = [k for k in new_keywords if k not in current_kws]
        
        if not to_add:
            return await update.message.reply_text("⚠️ Semua keyword tersebut sudah ada di radarmu.", reply_markup=get_radar_keyboard())

        # CEK TOTAL LIMIT (Hard Block jika melebihi 10)
        if len(current_kws) + len(to_add) > 10:
            return await update.message.reply_text(
                f"❌ *Gagal menambahkan radar!*\n\n"
                f"Limit maksimal adalah 10 keyword. Saat ini kamu sudah memiliki *{len(current_kws)}* keyword di database.\n\n"
                f"Silakan hapus keyword lama terlebih dahulu menggunakan tombol ➖ *Hapus Radar*.",
                parse_mode="Markdown",
                reply_markup=get_radar_keyboard()
            )
        
        added_kws = []
        # Proses insert jika lolos limit
        for kw in to_add:
            try:
                await db(lambda: supabase.table("user_radars").insert({"user_id": user_id, "keyword": kw}).execute())
                added_kws.append(kw)
            except Exception:
                pass
                
        # Segarkan cache di RAM kalau ada yang berhasil ditambahkan
        if added_kws:
            await update_radar_cache() 
            reply_msg = "✅ *Berhasil ditambahkan:*\n- " + "\n- ".join([f"`{k}`" for k in added_kws])
            return await update.message.reply_text(reply_msg, parse_mode="Markdown", reply_markup=get_radar_keyboard())
        else:
            return await update.message.reply_text("❌ Terjadi kesalahan sistem saat menyimpan radar.", reply_markup=get_radar_keyboard())

    if keyboard_state == KEYBOARD_STATE_RADAR_REMOVE:
        context.user_data.pop("keyboard_state", None)
        keyword = text_content.lower()
        try:
            await db(lambda: supabase.table("user_radars").delete().eq("user_id", user_id).eq("keyword", keyword).execute())
            await update_radar_cache() # Segarkan cache di RAM
            return await update.message.reply_text(f"🗑️ Keyword `{keyword}` dihapus dari radar!", parse_mode="Markdown", reply_markup=get_radar_keyboard())
        except Exception:
            return await update.message.reply_text("❌ Gagal menghapus radar.", reply_markup=get_radar_keyboard())
    
    if keyboard_state == KEYBOARD_STATE_VERIFY_SELLER:
        context.user_data.pop("keyboard_state", None)
        if not update.message.text:
            context.user_data["keyboard_state"] = KEYBOARD_STATE_VERIFY_SELLER
            return await update.message.reply_text("❌ Form verifikasi harus dikirim dalam bentuk teks.", reply_markup=get_cancel_keyboard())
        await submit_seller_verification(update, context)
        return

    if keyboard_state == KEYBOARD_STATE_CHECK_PENIPU:
        context.user_data.pop("keyboard_state", None)
        if not text_content:
            return await update.message.reply_text("❌ Target tidak boleh kosong.", reply_markup=get_main_keyboard())
        await process_check_penipu(update, context, raw_target=text_content)
        await update.message.reply_text("✅ Pengecekan selesai. Kembali ke menu utama.", reply_markup=get_main_keyboard())
        return

    if update.message.text and "verifikasi seller" in text_content_lower:
        await start_seller_verification(update, context)
        return

    if update.message.text and "check penipu" in text_content_lower:
        context.user_data["keyboard_state"] = KEYBOARD_STATE_CHECK_PENIPU
        await update.message.reply_text(
            "🔍 *Check Penipu*\n\nKetik ID atau username target. Contoh: `@username` atau `123456789`.",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        return

    if update.message.text and ("profile" in text_content_lower or "profil" in text_content_lower):
        await cek_profile(update, context)
        return

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

                post_url = f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}"
                asyncio.create_task(trigger_radar_notifications(context, text_content, post_url))

                keyboard = [[InlineKeyboardButton("Lihat Pesan Kamu", url=f"https://t.me/{CHANNEL_ID[1:]}/{message_sent.message_id}")]]
                await update.message.reply_text("Pesan kamu telah dikirim ke channel! 🪶\n\nJangan lupa kepoin channel base ya!", reply_markup=InlineKeyboardMarkup(keyboard))
                try: await db(lambda: supabase.table("menfess_map").insert({"post_id": message_sent.message_id, "sender_user_id": user_id}).execute())
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

    if data.startswith("seller|"):
        await query.answer()
        try:
            _, action, raw_user_id = data.split("|", 2)
            user_id = int(raw_user_id)
        except Exception:
            return await query.edit_message_text(f"{query.message.text}\n\n❌ STATUS: CALLBACK TIDAK VALID")

        admin_id = update.effective_user.id if update.effective_user else None

        if action == "A":
            try:
                await context.bot.set_chat_member_tag(
                    chat_id=GROUP_ID_DISKUSI,
                    user_id=user_id,
                    tag=SELLER_VERIFIED_TITLE
                )
                await set_user_seller_status(user_id, "verified")
                await query.edit_message_text(f"{query.message.text}\n\n✅ STATUS: DISETUJUI\n🏷️ Title komentar: {SELLER_VERIFIED_TITLE}")
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ Verifikasi seller kamu disetujui! Title komentar kamu sekarang: {SELLER_VERIFIED_TITLE}",
                    reply_markup=get_main_keyboard()
                )
            except Exception as e:
                logger.error(f"Gagal approve verifikasi seller {user_id}: {e}")
                await query.edit_message_text(
                    f"{query.message.text}\n\n❌ STATUS: GAGAL DISETUJUI\nGagal mengubah title. Pastikan bot punya izin Manage Tags/Kelola Peran Anggota dan user sudah join grup diskusi."
                )
            return

        if action == "R":
            await set_user_seller_status(user_id, "rejected")
            await query.edit_message_text(f"{query.message.text}\n\n❌ STATUS: DITOLAK")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Maaf, verifikasi seller kamu ditolak oleh admin. Silakan lengkapi/benahi data lalu ajukan ulang.",
                    reply_markup=get_main_keyboard()
                )
            except Exception:
                pass
            return

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

                post_url = f"https://t.me/{CHANNEL_ID[1:]}/{sent_msg.message_id}"
                menfess_text = original_msg.text or original_msg.caption or ""
                asyncio.create_task(trigger_radar_notifications(context, menfess_text, post_url))

                if not comsect_on: CACHE_COMSECT_OFF.add(sent_msg.message_id)
                log_msg = f"📌 Log Menfess (Manual Approved):\n🆔 Pengirim ID: `{user_id}`\n⚙️ Comsect: {'ON' if comsect_on else 'OFF'}"
                await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")
                try: await db(lambda: supabase.table("menfess_map").insert({"post_id": sent_msg.message_id, "sender_user_id": user_id}).execute())
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
            response = await db(lambda: supabase.table("commands").select("content").eq("name", reply_text.split()[0]).execute())
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
            try: await db(lambda: supabase.table("menfess_map").update({"discussion_message_id": msg.message_id}).eq("post_id", post_id).execute())
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
            response = await db(lambda: supabase.table("menfess_map").select("sender_user_id, post_id").eq("discussion_message_id", replied_msg_id).execute())
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
        try: await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "true"}).execute())
        except Exception as e: logger.error(f"Gagal menyimpan status bot_active: {e}")
        await update.message.reply_text("✅ Bot telah diaktifkan kembali.")

async def close_bot(update: Update, context: CallbackContext):
    global bot_active
    if update.effective_chat.id == ADMIN_GROUP_ID:
        bot_active = False
        try: await db(lambda: supabase.table("bot_settings").upsert({"key": "bot_active", "value": "false"}).execute())
        except Exception as e: logger.error(f"Gagal menyimpan status bot_active: {e}")
        await update.message.reply_text("⏸️ Bot telah dipause.")

async def get_group_id(update: Update, context: CallbackContext):
    await update.message.reply_text(f"🆔 ID: `{update.effective_chat.id}`\n🏷️ Nama: {update.effective_chat.title or 'Private'}", parse_mode="Markdown")

async def get_all_user_ids():
    try:
        response = await db(lambda: supabase.table("users").select("user_id").execute())
        return [row["user_id"] for row in response.data] if hasattr(response, "data") and response.data else []
    except Exception: return []

async def menu(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private": return
    menu_text = "𔐼 *Bazarfess:* [@bazarfess](https://t.me/bazarfess)\n𔐼 *LPM Bazar:* [@lpmbazar](https://t.me/lpmbazar)\n𔐼 *Info Base:* [@rekapbazar](https://t.me/rekapbazar)\n\n"
    await update.message.reply_text(menu_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

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
    failed_users = [] # List untuk menyimpan ID user yang gagal
    
    status_msg = await update.message.reply_text(f"⏳ *Memulai proses broadcast forward ke {total_users} user...*\nMohon tunggu ya!", parse_mode="Markdown")
    
    for i, user_id in enumerate(user_list, 1):
        try:
            await context.bot.forward_message(chat_id=user_id, from_chat_id=f"@{channel_username}", message_id=int(message_id))
            sc += 1
        except Exception: 
            fc += 1
            failed_users.append(str(user_id)) # Masukkan ID ke list jika gagal
            
        if i % 20 == 0:
            try: await status_msg.edit_text(f"⏳ *Sedang memproses broadcast... ({i}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
            except Exception: pass
        await asyncio.sleep(0.05)
        
    await status_msg.edit_text(f"✅ *Broadcast Forward Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    # Kirim file .txt jika ada user yang gagal
    if failed_users:
        failed_text = "\n".join(failed_users)
        file = io.BytesIO(failed_text.encode('utf-8'))
        file.name = "failed_broadcast_forward.txt"
        await context.bot.send_document(
            chat_id=update.effective_chat.id, 
            document=file, 
            caption=f"📄 Terdapat {len(failed_users)} user yang gagal menerima broadcast forward."
        )

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID or not context.args: return await update.message.reply_text("Format: /broadcast <teks>")
    message_text = " ".join(context.args)
    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database untuk dibroadcast.")

    sc, fc = 0, 0
    failed_users = [] # List untuk menyimpan ID user yang gagal
    
    status_msg = await update.message.reply_text(f"⏳ *Memulai proses broadcast ke {total_users} user...*\nMohon tunggu ya!", parse_mode="Markdown")
    
    for i, user_id in enumerate(user_list, 1):
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            sc += 1
        except Exception: 
            fc += 1
            failed_users.append(str(user_id)) # Masukkan ID ke list jika gagal
            
        if i % 20 == 0:
            try: await status_msg.edit_text(f"⏳ *Sedang memproses broadcast... ({i}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
            except Exception: pass
        await asyncio.sleep(0.05)
        
    await status_msg.edit_text(f"✅ *Broadcast Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")

    # Kirim file .txt jika ada user yang gagal
    if failed_users:
        failed_text = "\n".join(failed_users)
        file = io.BytesIO(failed_text.encode('utf-8'))
        file.name = "failed_broadcast.txt"
        await context.bot.send_document(
            chat_id=update.effective_chat.id, 
            document=file, 
            caption=f"📄 Terdapat {len(failed_users)} user yang gagal menerima pesan broadcast."
        )

async def push_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    
    # Cek apakah ada teks yang diketik setelah command
    if not context.args: 
        return await update.message.reply_text("⚠️ Format salah! Gunakan: `/pushkeyboard <teks pesan>`\nContoh: `/pushkeyboard 🚀 Ada update menu baru nih, cek di bawah ya!`", parse_mode="Markdown")
        
    # Gabungkan semua kata setelah command menjadi satu string
    info_text = " ".join(context.args)
    
    user_list = await get_all_user_ids()
    total_users = len(user_list)
    if total_users == 0: return await update.message.reply_text("⚠️ Tidak ada user di database untuk diupdate.")

    sc, fc = 0, 0
    status_msg = await update.message.reply_text(f"⏳ *Memulai push update keyboard ke {total_users} user...*\nMohon tunggu ya!", parse_mode="Markdown")
    
    # Ambil layout keyboard terbaru
    new_keyboard = get_main_keyboard()
    
    for i, user_id in enumerate(user_list, 1):
        try:
            # Kirim pesan notifikasi sekaligus memaksa client Telegram mereka merender ulang keyboard
            await context.bot.send_message(
                chat_id=user_id, 
                text=info_text,
                parse_mode="Markdown",
                reply_markup=new_keyboard
            )
            sc += 1
        except Exception: 
            fc += 1
            
        if i % 20 == 0:
            try: await status_msg.edit_text(f"⏳ *Sedang memproses update keyboard... ({i}/{total_users})*\n✅ Berhasil: {sc}\n❌ Gagal: {fc}", parse_mode="Markdown")
            except Exception: pass
        await asyncio.sleep(0.05)
        
    await status_msg.edit_text(f"✅ *Push Keyboard Selesai!*\n👥 Total Target: {total_users}\n✅ Berhasil diupdate: {sc}\n❌ Gagal (Bot diblokir): {fc}", parse_mode="Markdown")

async def add_command(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if update.message.reply_to_message:
        if not context.args:
            return await update.message.reply_text("Format (reply): /addcommand <nama>")
        command_name = context.args[0]
        command_content = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
    else:
        if len(context.args) < 2: return await update.message.reply_text("Format: /addcommand <nama> <isi>")
        command_name, command_content = context.args[0], " ".join(context.args[1:])
    command_name = command_name if command_name.startswith("/") else "/" + command_name
    try:
        await db(lambda: supabase.table("commands").upsert({"name": command_name, "content": command_content}).execute())
        await update.message.reply_text(f"✅ `{command_name}` disimpan!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def delete_command(update: Update, context: CallbackContext) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not context.args: return await update.message.reply_text("Format: /deletecommand <nama>")
    command_name = context.args[0] if context.args[0].startswith("/") else "/" + context.args[0]
    try:
        await db(lambda: supabase.table("commands").delete().eq("name", command_name).execute())
        await update.message.reply_text(f"✅ `{command_name}` dihapus!", parse_mode='Markdown')
    except Exception: await update.message.reply_text("❌ Gagal.")

async def settings(update: Update, context: CallbackContext):
    if update.effective_chat.id != ADMIN_GROUP_ID: return
    channels_text = "\n".join([f"𔐼 {c}" for c in required_channels]) if required_channels else "–"
    hashtags_text = "\n".join([f"𔐼 `{h}`" for h in CACHE_HASHTAGS]) if CACHE_HASHTAGS else "–"
    global MENFESS_MODE
    try:
        response = await db(lambda: supabase.table("commands").select("name, content").execute())
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
    application = Application.builder().token(BOT_TOKEN).post_init(on_startup).concurrent_updates(True).build()

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
    application.add_handler(CommandHandler(['profile', 'profil'], cek_profile))
    application.add_handler(CommandHandler(['verifseller', 'verifikasi_seller'], start_seller_verification))
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
    application.add_handler(CommandHandler('pushkeyboard', push_keyboard))
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
