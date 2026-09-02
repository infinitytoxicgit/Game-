import asyncio
import html
import io
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus, ParseMode
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    ChatMemberUpdated
)

# ============================================================
# CONFIG & HARDCODED CREDENTIALS
# ============================================================

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_ID = 35218869
API_HASH = "80baadcfd00a39a0ff1f5f529d23156f"
OWNER_ID = 8564072723
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

START_IMG = "https://graph.org/file/7c0c03d68308f0c5dad42-ddb933df03f0ff0632.jpg"
SUPPORT_GC = "https://t.me/Roohi_Soul_Gc"
ADD_ME_URL = "https://t.me/Jumbles_Words_Bot?startgroup=true"
MUSIC_BOT_URL = "https://t.me/Roohi_Queen_Bot?start=_tgr_yN-6yUs4ZmRh"

app = Client(
    "advanced_jumble_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

DB = sqlite3.connect("jumble_game.db", check_same_thread=False)
DB.row_factory = sqlite3.Row
LOCK = asyncio.Lock()

# ============================================================
# WORD BANK
# ============================================================

DEFAULT_EASY = """
apple banana orange mango table chair house water school friend family
happy garden flower animal window bottle mobile computer summer winter
river music movie player football cricket doctor teacher market village
country morning evening coffee bread pizza camera phone pencil paper
train bus road car earth world light night star cloud rain green blue
black white tiger lion horse rabbit monkey fish bird tree fruit
""".split()

DEFAULT_MEDIUM = """
adventure beautiful knowledge education important dangerous different
experience friendship happiness technology information internet
mountain waterfall sunshine keyboard hospital university restaurant
football cricket championship tournament engineer scientist medicine
history geography language computer network application database
security password community discussion entertainment television
photography creativity imagination discovery opportunity challenge
journey traveler vacation airport railway newspaper magazine
""".split()

DEFAULT_HARD = """
extraordinary responsibility communication determination independence
international transformation understanding environment intelligence
architecture investigation recommendation administration opportunity
entrepreneurship cryptocurrency cybersecurity authentication
programming mathematics biotechnology astrophysics psychology
philosophy civilization transportation infrastructure globalization
misunderstanding pronunciation encyclopedia experimentation
electromagnetism thermodynamics interoperability decentralization
""".split()

# ============================================================
# DATABASE SETUP & MIGRATIONS
# ============================================================

DB.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    name TEXT,
    points INTEGER DEFAULT 0,
    solved INTEGER DEFAULT 0,
    best_streak INTEGER DEFAULT 0,
    streak INTEGER DEFAULT 0,
    fight_wins INTEGER DEFAULT 0,
    fight_losses INTEGER DEFAULT 0,
    bet_wins INTEGER DEFAULT 0,
    bet_losses INTEGER DEFAULT 0,
    is_private INTEGER DEFAULT 0,
    last_daily REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS auth_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    name TEXT,
    added_at REAL
);

CREATE TABLE IF NOT EXISTS custom_words (
    difficulty TEXT,
    word TEXT,
    PRIMARY KEY(difficulty, word)
);

CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY,
    easy INTEGER DEFAULT 120,
    medium INTEGER DEFAULT 300,
    hard INTEGER DEFAULT 600,
    default_diff TEXT DEFAULT 'medium',
    is_active INTEGER DEFAULT 1,
    auto_delete INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value INTEGER
);

CREATE TABLE IF NOT EXISTS group_adders (
    chat_id INTEGER PRIMARY KEY,
    user_id INTEGER,
    added_at REAL
);

CREATE TABLE IF NOT EXISTS group_bonus (
    chat_id INTEGER PRIMARY KEY,
    user_id INTEGER,
    claimed_at REAL
);

CREATE TABLE IF NOT EXISTS games (
    chat_id INTEGER PRIMARY KEY,
    difficulty TEXT,
    word TEXT,
    puzzle_id INTEGER,
    started REAL,
    expires REAL,
    message_id INTEGER,
    solved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS used_words (
    chat_id INTEGER,
    difficulty TEXT,
    word TEXT,
    PRIMARY KEY(chat_id, difficulty, word)
);

CREATE TABLE IF NOT EXISTS puzzle_hints (
    chat_id INTEGER,
    puzzle_id INTEGER,
    user_id INTEGER,
    hints_used INTEGER DEFAULT 0,
    revealed_indices TEXT DEFAULT '',
    PRIMARY KEY(chat_id, puzzle_id, user_id)
);

CREATE TABLE IF NOT EXISTS score_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    chat_id INTEGER,
    points INTEGER,
    timestamp REAL
);
""")
DB.commit()

def run_migrations():
    defaults = {
        "points_easy": 10,
        "points_medium": 20,
        "points_hard": 30,
        "hints_easy": 3,
        "hints_medium": 3,
        "hints_hard": 3,
        "daily_points": 50,
        "bonus_points": 100
    }
    for k, v in defaults.items():
        DB.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))

    cols = [c[1] for c in DB.execute("PRAGMA table_info(settings)").fetchall()]
    if "default_diff" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN default_diff TEXT DEFAULT 'medium'")
    if "is_active" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN is_active INTEGER DEFAULT 1")
    if "auto_delete" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN auto_delete INTEGER DEFAULT 0")
    
    user_cols = [c[1] for c in DB.execute("PRAGMA table_info(users)").fetchall()]
    if "fight_wins" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN fight_wins INTEGER DEFAULT 0")
    if "fight_losses" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN fight_losses INTEGER DEFAULT 0")
    if "bet_wins" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN bet_wins INTEGER DEFAULT 0")
    if "bet_losses" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN bet_losses INTEGER DEFAULT 0")
    if "is_private" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN is_private INTEGER DEFAULT 0")
    if "last_daily" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN last_daily REAL DEFAULT 0")

    DB.commit()

run_migrations()

WORDS = {
    "easy": list(set(w.lower() for w in DEFAULT_EASY if len(w) >= 3)),
    "medium": list(set(w.lower() for w in DEFAULT_MEDIUM if len(w) >= 3)),
    "hard": list(set(w.lower() for w in DEFAULT_HARD if len(w) >= 3))
}

custom_rows = DB.execute("SELECT difficulty, word FROM custom_words").fetchall()
for row in custom_rows:
    diff = row["difficulty"].lower()
    w = row["word"].lower().strip()
    if diff in WORDS and w not in WORDS[diff]:
        WORDS[diff].append(w)

# ============================================================
# HELPERS & PERMISSIONS
# ============================================================

def ensure_user(user):
    if not user:
        return
    DB.execute("""
        INSERT INTO users(user_id, username, name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            name=excluded.name
    """, (
        user.id,
        user.username or "",
        user.first_name or "Player"
    ))
    DB.commit()

def get_user(user_id):
    return DB.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def is_owner(user_id):
    if not user_id:
        return False
    return int(user_id) == int(OWNER_ID)

def is_authed(user_id):
    if not user_id:
        return False
    if is_owner(user_id):
        return True
    row = DB.execute("SELECT user_id FROM auth_users WHERE user_id=?", (int(user_id),)).fetchone()
    return bool(row)

def get_global_config(key, default_val):
    row = DB.execute("SELECT value FROM bot_config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default_val

def set_global_config(key, val):
    DB.execute("""
        INSERT INTO bot_config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, val))
    DB.commit()

async def is_admin_or_owner(chat, user_id):
    if is_owner(user_id):
        return True
    if chat.type in (ChatType.PRIVATE,):
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False

def get_settings(chat_id):
    row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        DB.execute("""
            INSERT INTO settings(chat_id, easy, medium, hard, default_diff, is_active, auto_delete)
            VALUES (?, 120, 300, 600, 'medium', 1, 0)
            ON CONFLICT(chat_id) DO NOTHING
        """, (chat_id,))
        DB.commit()
        row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    return row

def is_group(message):
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

def get_mention(user_obj=None, user_id=None, first_name=None, username=None):
    if user_obj:
        u_id = user_obj.id
        f_name = user_obj.first_name or "Player"
        u_name = user_obj.username
    else:
        u_id = user_id
        f_name = first_name or "Player"
        u_name = username

    clean_name = html.escape(str(f_name))
    if u_name:
        return f"<a href='https://t.me/{u_name}'>{clean_name}</a>"
    return f"<a href='tg://openmessage?user_id={u_id}'>{clean_name}</a>"

def clean_answer(text):
    return "".join(c.lower() for c in str(text) if c.isalnum())

def jumble_word(word):
    letters = list(word)
    for _ in range(50):
        random.shuffle(letters)
        result = "".join(letters)
        if result != word and result[::-1] != word:
            return result.upper()
    return "".join(letters).upper()

def choose_word(chat_id, difficulty):
    pool = WORDS.get(difficulty, [])[:]
    used = {
        row["word"]
        for row in DB.execute(
            "SELECT word FROM used_words WHERE chat_id=? AND difficulty=?",
            (chat_id, difficulty)
        ).fetchall()
    }
    available = [w for w in pool if w not in used]

    if not available:
        DB.execute("DELETE FROM used_words WHERE chat_id=? AND difficulty=?", (chat_id, difficulty))
        DB.commit()
        available = pool

    if not available:
        return "JUMBLE"

    word = random.choice(available)
    DB.execute("INSERT OR IGNORE INTO used_words(chat_id, difficulty, word) VALUES (?, ?, ?)", (chat_id, difficulty, word))
    DB.commit()
    return word

def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
    ]
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def make_puzzle_image(jumbled, mode_tag, puzzle_id):
    img = Image.new("RGB", (1200, 650), "#10131a")
    draw = ImageDraw.Draw(img)

    title_font = get_font(55)
    small_font = get_font(35)

    text_len = len(jumbled)
    if text_len <= 7:
        display_text = "   ".join(jumbled)
        word_font = get_font(85)
    elif text_len <= 11:
        display_text = "  ".join(jumbled)
        word_font = get_font(65)
    elif text_len <= 15:
        display_text = " ".join(jumbled)
        word_font = get_font(50)
    else:
        display_text = " ".join(jumbled)
        word_font = get_font(38)

    draw.text((600, 70), "🧩 JUMBLE WORD", anchor="mm", font=title_font, fill="white")
    draw.text((600, 300), display_text, anchor="mm", font=word_font, fill="#00e5ff")
    draw.text((600, 480), f"{mode_tag.upper()}  •  PUZZLE #{puzzle_id}", anchor="mm", font=small_font, fill="#ffffff")
    draw.text((600, 545), "Unscramble the letters!", anchor="mm", font=small_font, fill="#aaaaaa")

    bio = io.BytesIO()
    bio.name = f"puzzle_{puzzle_id}_{random.randint(100, 999)}.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

async def delete_after(msg: Message, delay: int = 5):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass

async def safe_delete_and_unpin(chat_id: int, message_id: int):
    if not message_id:
        return
    try:
        await app.unpin_chat_message(chat_id, message_id)
    except Exception:
        pass
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass

# ============================================================
# BOT ADDED TO GROUP LISTENER
# ============================================================

@app.on_chat_member_updated()
async def bot_added_handler(_, update: ChatMemberUpdated):
    if update.new_chat_member and update.new_chat_member.user and update.new_chat_member.user.is_self:
        if update.from_user and not update.from_user.is_bot:
            chat_id = update.chat.id
            user_id = update.from_user.id
            ensure_user(update.from_user)
            DB.execute("""
                INSERT INTO group_adders (chat_id, user_id, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id, added_at=excluded.added_at
            """, (chat_id, user_id, time.time()))
            DB.commit()

# ============================================================
# NORMAL GAME CORE
# ============================================================

def normal_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 𝐇ɪɴᴛ", callback_data="hint"),
            InlineKeyboardButton("⏭️ 𝐒ᴋɪᴘ", callback_data="skip")
        ],
        [
            InlineKeyboardButton("🆕 𝐍ᴇᴡ 𝐖ᴏʀᴅ", callback_data="newword")
        ]
    ])

async def start_game(chat_id, difficulty, message_or_chat):
    if chat_id in JUMBLE_FIGHT:
        return

    settings = get_settings(chat_id)
    if not settings["is_active"]:
        return

    old_game = DB.execute("SELECT message_id FROM games WHERE chat_id=?", (chat_id,)).fetchone()
    if old_game and settings["auto_delete"] and old_game["message_id"]:
        await safe_delete_and_unpin(chat_id, old_game["message_id"])

    DB.execute("DELETE FROM games WHERE chat_id=?", (chat_id,))

    word = choose_word(chat_id, difficulty)
    jumbled = jumble_word(word)
    puzzle_id = random.randint(10000, 99999)
    now = time.time()
    timer_val = settings[difficulty]
    reward_pts = get_global_config(f"points_{difficulty}", 10)
    hint_limit = get_global_config(f"hints_{difficulty}", 3)
    expires = now + timer_val

    DB.execute("""
        INSERT INTO games(chat_id, difficulty, word, puzzle_id, started, expires, message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, difficulty, word, puzzle_id, now, expires, 0))
    DB.commit()

    image = make_puzzle_image(jumbled, difficulty, puzzle_id)
    caption_text = (
        f"<blockquote>🧩 <b>𝐉ᴜᴍʙʟᴇ #{puzzle_id}</b>\n\n"
        f"🎯 <b>𝐃ɪғғɪᴄᴜʟᴛʏ:</b> <code>{difficulty.title()}</code>\n"
        f"⏱️ <b>𝐓ɪᴍᴇ:</b> <code>{timer_val // 60}m {timer_val % 60}s</code>\n"
        f"⭐ <b>𝐑ᴇᴡᴀʀᴅ:</b> <code>+{reward_pts} Points</code>\n"
        f"💡 <b>𝐇ɪɴᴛs:</b> <code>{hint_limit}/word</code></blockquote>\n\n"
        f"<blockquote>🔀 <i>𝐔ɴsᴄʀᴀᴍʙʟᴇ ᴛʜᴇ ʟᴇᴛᴛᴇʀs & ᴛʏᴘᴇ ɪɴ ᴄʜᴀᴛ!</i></blockquote>"
    )

    try:
        if isinstance(message_or_chat, Message):
            sent = await message_or_chat.reply_photo(photo=image, caption=caption_text, reply_markup=normal_keyboard(), parse_mode=ParseMode.HTML)
        else:
            sent = await app.send_photo(chat_id, photo=image, caption=caption_text, reply_markup=normal_keyboard(), parse_mode=ParseMode.HTML)

        DB.execute("UPDATE games SET message_id=? WHERE chat_id=?", (sent.id, chat_id))
        DB.commit()

        try:
            await sent.pin(disable_notification=True)
        except Exception:
            pass
    except Exception as e:
        print(f"Error sending puzzle to {chat_id}: {e}")

    asyncio.create_task(expire_game(chat_id, puzzle_id, expires, difficulty))

async def expire_game(chat_id, puzzle_id, expires, difficulty):
    await asyncio.sleep(max(0, expires - time.time()))
    if chat_id in JUMBLE_FIGHT:
        return

    row = DB.execute("SELECT * FROM games WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id)).fetchone()
    if not row or row["solved"]:
        return

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    s = get_settings(chat_id)
    if s["auto_delete"] and row["message_id"]:
        await safe_delete_and_unpin(chat_id, row["message_id"])

    try:
        exp_msg = await app.send_message(
            chat_id,
            f"<blockquote>⏰ <b>𝐓ɪᴍᴇ's 𝐔ᴘ!</b>\n\n"
            f"❌ <b>𝐍ᴏʙᴏᴅʏ sᴏʟᴠᴇᴅ ɪᴛ.</b>\n"
            f"✅ <b>𝐀ɴsᴡᴇʀ:</b> <code>{row['word'].upper()}</code>\n\n"
            f"🔄 <i>𝐍ᴇxᴛ ᴘᴜᴢᴢʟᴇ sᴛᴀʀᴛɪɴɢ ɪɴ 3 sᴇᴄᴏɴᴅs...</i></blockquote>",
            parse_mode=ParseMode.HTML
        )
        if s["auto_delete"]:
            asyncio.create_task(delete_after(exp_msg, 4))
    except Exception:
        pass

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if chat_id not in JUMBLE_FIGHT and s["is_active"]:
        asyncio.create_task(start_game(chat_id, difficulty, chat_id))

# ============================================================
# JUMBLE FIGHT & JUMBLE BET FIGHT CORE (1v1)
# ============================================================

JUMBLE_FIGHT = {}
FIGHT_LOBBY = {}
REBET_LOBBY = {}

def fight_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 𝐇ɪɴᴛ", callback_data="fight_hint")
        ]
    ])

async def fight_timeout_task(chat_id, round_num, timer_duration):
    await asyncio.sleep(timer_duration)

    should_advance = False
    async with LOCK:
        game = JUMBLE_FIGHT.get(chat_id)
        if game and game["round"] == round_num:
            word = game["word"]
            
            s = get_settings(chat_id)
            if s["auto_delete"] and game.get("msg_id"):
                await safe_delete_and_unpin(chat_id, game["msg_id"])

            try:
                t_msg = await app.send_message(
                    chat_id,
                    f"<blockquote>⏰ <b>𝐑ᴏᴜɴᴅ {round_num} 𝐓ɪᴍᴇᴏᴜᴛ!</b>\n"
                    f"❌ <b>𝐊ɪsɪ ɴᴇ sᴏʟᴠᴇ ɴᴀʜɪ ᴋɪʏᴀ.</b>\n"
                    f"✅ <b>𝐀ɴsᴡᴇʀ:</b> <code>{word.upper()}</code>\n\n"
                    f"🔄 <i>𝐍ᴇxᴛ ʀᴏᴜɴᴅ sᴛᴀʀᴛɪɴɢ...</i></blockquote>",
                    parse_mode=ParseMode.HTML
                )
                if s["auto_delete"]:
                    asyncio.create_task(delete_after(t_msg, 4))
            except Exception:
                pass
            should_advance = True

    if should_advance:
        await asyncio.sleep(2.5)
        asyncio.create_task(fight_next(chat_id))

async def fight_next(chat_id):
    game = JUMBLE_FIGHT.get(chat_id)
    if not game:
        return

    curr = asyncio.current_task()
    if game.get("task") and game["task"] is not curr and not game["task"].done():
        try:
            game["task"].cancel()
        except Exception:
            pass

    game["round"] += 1
    if game["round"] > 10:
        await finish_fight(chat_id)
        return

    diff = game["difficulty"]
    word = random.choice(WORDS[diff])
    jumbled = jumble_word(word)

    game["word"] = word
    game["expires"] = time.time() + game["timer"]
    game["round_hints"] = defaultdict(lambda: {"count": 0, "indices": []})

    fight_tag = "BET FIGHT" if game.get("is_bet") else "FIGHT"
    image = make_puzzle_image(jumbled, f"{fight_tag} {diff.upper()}", game["round"])
    
    title_header = "💰 <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐁𝐄𝐓 𝐅𝐈𝐆𝐇𝐓" if game.get("is_bet") else "⚔️ <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐅𝐈𝐆𝐇𝐓"
    extra_info = f"\n💵 <b>𝐁ᴇᴛ:</b> <code>{game.get('bet_amount')} pts</code>" if game.get("is_bet") else ""

    try:
        sent = await app.send_photo(
            chat_id,
            photo=image,
            caption=(
                f"<blockquote>{title_header} — 𝐑𝐎𝐔𝐍𝐃 {game['round']}/10</b>\n\n"
                f"🎯 <b>𝐃ɪғғɪᴄᴜʟᴛʏ:</b> <code>{diff.title()}</code>\n"
                f"⏱️ <b>𝐓ɪᴍᴇ:</b> <code>{game['timer']}s</code>{extra_info}\n"
                f"🔀 <b>𝐒ᴏʟᴠᴇ ғᴀsᴛᴇsᴛ!</b>\n"
                f"👥 <b>𝐏ʟᴀʏᴇʀs:</b> {game['mentions'][game['players'][0]]} 🆚 {game['mentions'][game['players'][1]]}</blockquote>"
            ),
            reply_markup=fight_keyboard(),
            parse_mode=ParseMode.HTML
        )
        game["msg_id"] = sent.id
        try:
            await sent.pin(disable_notification=True)
        except Exception:
            pass
    except Exception as e:
        print(f"Fight send error: {e}")

    game["task"] = asyncio.create_task(fight_timeout_task(chat_id, game["round"], game["timer"]))

async def finish_fight(chat_id):
    game = JUMBLE_FIGHT.pop(chat_id, None)
    if not game:
        return

    curr = asyncio.current_task()
    if game.get("task") and game["task"] is not curr and not game["task"].done():
        try:
            game["task"].cancel()
        except Exception:
            pass

    s = get_settings(chat_id)
    if s["auto_delete"] and game.get("msg_id"):
        await safe_delete_and_unpin(chat_id, game["msg_id"])

    p1, p2 = game["players"]
    s1, s2 = game["scores"][p1], game["scores"][p2]
    is_bet = game.get("is_bet", False)
    bet_amt = game.get("bet_amount", 0)
    is_rebet = game.get("is_rebet", False)

    if s1 > s2:
        winner, loser = p1, p2
        w_score, l_score = s1, s2
    elif s2 > s1:
        winner, loser = p2, p1
        w_score, l_score = s2, s1
    else:
        winner = loser = None

    m1 = game["mentions"][p1]
    m2 = game["mentions"][p2]
    end_kb = None

    if not is_bet:
        if winner:
            DB.execute("UPDATE users SET fight_wins=fight_wins+1 WHERE user_id=?", (winner,))
            DB.execute("UPDATE users SET fight_losses=fight_losses+1 WHERE user_id=?", (loser,))
            DB.commit()

        result = f"<blockquote>🏁 <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐅𝐈𝐆𝐇𝐓 𝐎𝐕𝐄𝐑!</b>\n\n👤 {m1} — <b>{s1} pts</b>\n👤 {m2} — <b>{s2} pts</b>\n\n"
        if winner:
            result += f"🏆 <b>𝐌ᴀᴛᴄʜ 𝐖ɪɴɴᴇʀ:</b> {game['mentions'][winner]} 🎉</blockquote>"
        else:
            result += "🤝 <b>𝐌ᴀᴛᴄʜ 𝐃ʀᴀᴡ!</b></blockquote>"

    else:
        if winner:
            if is_rebet:
                total_rematch_pot = bet_amt * 2
                comeback_bonus = 100
                total_payout = total_rematch_pot + comeback_bonus

                DB.execute("UPDATE users SET points=points+?, bet_wins=bet_wins+1 WHERE user_id=?", (total_payout, winner))
                DB.execute("UPDATE users SET bet_losses=bet_losses+1 WHERE user_id=?", (loser,))
                DB.commit()

                result = (
                    f"<blockquote>💰 <b>25% 𝐂𝐎𝐌𝐄𝐁𝐀𝐂𝐊 𝐑𝐄-𝐁𝐄𝐓 𝐅𝐈𝐆𝐇𝐓 𝐎𝐕𝐄𝐑!</b>\n\n"
                    f"👤 {game['mentions'][winner]} — <b>{w_score} pts</b> (WINNER)\n"
                    f"👤 {game['mentions'][loser]} — <b>{l_score} pts</b>\n\n"
                    f"🔥 <b>Comeback Payout:</b>\n"
                    f"• 25% + 25% Stake: <code>+{total_rematch_pot} pts</code>\n"
                    f"• Comeback Reward: <code>+100 stars/pts</code>\n"
                    f"🏆 <b>Total Reward:</b> <code>+{total_payout} points</code> to {game['mentions'][winner]}!\n\n"
                    f"💀 {game['mentions'][loser]} rematch haar gaya aur use 0 points mile.</blockquote>"
                )
            else:
                total_pot = bet_amt * 2
                win_reward = int(total_pot * 0.75)
                loser_cashback = total_pot - win_reward
                rebet_stake = int(bet_amt * 0.25)

                DB.execute("UPDATE users SET points=points+?, bet_wins=bet_wins+1 WHERE user_id=?", (win_reward, winner))
                DB.execute("UPDATE users SET points=points+?, bet_losses=bet_losses+1 WHERE user_id=?", (loser_cashback, loser))
                DB.commit()

                REBET_LOBBY[chat_id] = {
                    "original_winner": winner,
                    "original_loser": loser,
                    "rebet_amount": rebet_stake,
                    "difficulty": game["difficulty"],
                    "timer": game["timer"],
                    "winner_mention": game['mentions'][winner],
                    "loser_mention": game['mentions'][loser],
                    "orig_stake": bet_amt
                }

                end_kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(f"🔁 25% Re-Bet ({rebet_stake} pts) + 100 Bonus", callback_data="rebet_challenge")
                    ]
                ])

                result = (
                    f"<blockquote>💰 <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐁𝐄𝐓 𝐅𝐈𝐆𝐇𝐓 𝐎𝐕𝐄𝐑!</b>\n\n"
                    f"👤 {game['mentions'][winner]} — <b>{w_score} pts</b> (WINNER)\n"
                    f"👤 {game['mentions'][loser]} — <b>{l_score} pts</b>\n\n"
                    f"🏆 <b>75% 𝐖ɪɴɴᴇʀ 𝐑ᴇᴡᴀʀᴅ:</b> <code>+{win_reward} points</code> ({game['mentions'][winner]})\n"
                    f"🛡️ <b>25% 𝐋ᴏsᴇʀ 𝐂ᴀsʜʙᴀᴄᴋ:</b> <code>+{loser_cashback} points</code> ({game['mentions'][loser]})\n\n"
                    f"👉 {game['mentions'][loser]} chahe toh <b>25% Re-bet</b> karke 25%+25% pot aur <b>+100 Comeback Stars</b> jeet sakta hai!</blockquote>"
                )
        else:
            DB.execute("UPDATE users SET points=points+? WHERE user_id=?", (bet_amt, p1))
            DB.execute("UPDATE users SET points=points+? WHERE user_id=?", (bet_amt, p2))
            DB.commit()
            result = (
                f"<blockquote>🤝 <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐁𝐄𝐓 𝐅𝐈𝐆𝐇𝐓 𝐃𝐑𝐀𝐖!</b>\n\n"
                f"👤 {m1} — <b>{s1} pts</b>\n"
                f"👤 {m2} — <b>{s2} pts</b>\n\n"
                f"Dono players ko unka stake <code>{bet_amt} points</code> wapas refund kar diya gaya hai.</blockquote>"
            )

    await app.send_message(chat_id, result, reply_markup=end_kb, parse_mode=ParseMode.HTML)

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if s["is_active"]:
        await app.send_message(chat_id, "<blockquote>🔄 <i>𝐑ᴇsᴜᴍɪɴɢ ɴᴏʀᴍᴀʟ 𝐉ᴜᴍʙʟᴇ 𝐆ᴀᴍᴇ...</i></blockquote>", parse_mode=ParseMode.HTML)
        asyncio.create_task(start_game(chat_id, s["default_diff"], chat_id))

# ============================================================
# COMMAND HANDLERS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    ensure_user(message.from_user)
    text = (
        "<blockquote>🧩 <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐓ᴏ 𝐀ᴅᴠᴀɴᴄᴇᴅ 𝐉ᴜᴍʙʟᴇ 𝐁ᴏᴛ!</b></blockquote>\n\n"
        "<blockquote>🎮 <b>𝐆ᴀᴍᴇ 𝐂ᴏᴍᴍᴀɴᴅs:</b>\n"
        "• <code>/jumble</code> — 𝐒ᴛᴀʀᴛ 𝐀ᴜᴛᴏ-ʟᴏᴏᴘ 𝐉ᴜᴍʙʟᴇ 𝐆ᴀᴍᴇ\n"
        "• <code>/jumblefight @user</code> — 1v1 𝐁ᴀᴛᴛʟᴇ 𝐌ᴏᴅᴇ (ᴡɪᴛʜ 𝐀ᴄᴄᴇᴘᴛ 𝐆ᴀᴛᴇ)\n"
        "• <code>/jumblebetfight [mode] [amount] @user</code> — 1v1 𝐁ᴇᴛ 𝐁ᴀᴛᴛʟᴇ\n"
        "• <code>/settings</code> — 𝐀ᴅᴍɪɴ 𝐏ᴀɴᴇʟ (𝐒ᴛᴀʀᴛ/𝐒ᴛᴏᴘ, 𝐌ᴏᴅᴇ, 𝐀ᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ)</blockquote>\n\n"
        "<blockquote>🎁 <b>𝐅ʀᴇᴇ 𝐏ᴏɪɴᴛs & 𝐑ᴇᴡᴀʀᴅs:</b>\n"
        "• <code>/daily</code> — 𝐂ʟᴀɪᴍ 𝐃ᴀɪʟʏ 𝐁ᴏɴᴜs 𝐏ᴏɪɴᴛs ɪɴ 𝐃ᴍ (ᴇᴠᴇʀʏ 24ʜ)\n"
        "• <code>/bonus</code> — 𝐂ʟᴀɪᴍ 𝐆ʀᴏᴜᴘ 𝐀ᴅᴅɪᴛɪᴏɴ 𝐁ᴏɴᴜs (ᴡʜᴇɴ 𝐁ᴏᴛ ɪs 𝐀ᴅᴅᴇᴅ ᴀs 𝐀ᴅᴍɪɴ)</blockquote>\n\n"
        "<blockquote>🛡️ <b>𝐏ʀɪᴠᴀᴄʏ 𝐒ᴇᴛᴛɪɴɢs:</b>\n"
        "• <code>/private</code> — 𝐇ɪᴅᴇ 𝐈𝐃/𝐓ᴀɢ ᴏɴ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅ (𝐍ᴀᴍᴇ ᴏɴʟʏ)\n"
        "• <code>/public</code> — 𝐒ʜᴏᴡ 𝐓ᴀɢ & 𝐈𝐃 ᴏɴ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅ</blockquote>\n\n"
        "<blockquote>📊 <b>𝐒ᴛᴀᴛs & 𝐑ᴀɴᴋɪɴɢs:</b>\n"
        "• <code>/stats</code> — 𝐘ᴏᴜʀ 𝐏ᴇʀғᴏʀᴍᴀɴᴄᴇ\n"
        "• <code>/leaderboard</code> — 𝐃ᴀɪʟʏ, 𝐖ᴇᴇᴋʟʏ, 𝐌ᴏɴᴛʜʟʏ & 𝐆ʟᴏʙᴀʟ 𝐑ᴀɴᴋs\n"
        "• <code>/help</code> — 𝐅ᴜʟʟ 𝐁ᴏᴛ 𝐆ᴜɪᴅᴇ</blockquote>"
    )

    dm_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 𝐒ᴜᴘᴘᴏʀᴛ", url=SUPPORT_GC),
            InlineKeyboardButton("➕ 𝐀ᴅᴅ 𝐌ᴇ", url=ADD_ME_URL)
        ],
        [
            InlineKeyboardButton("˹ 𓆩ℛᴏ֟፝ᴏʜɪ ꭙ 𝐌ᴜ֟፝sɪᴄ𓆪˼ ♪", url=MUSIC_BOT_URL)
        ]
    ])

    if message.chat.type in (ChatType.PRIVATE,):
        try:
            await message.reply_photo(photo=START_IMG, caption=text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    is_user_auth = is_authed(message.from_user.id) if message.from_user else False
    text = (
        "<blockquote>🧩 <b>𝐉ᴜᴍʙʟᴇ 𝐂ᴏᴍᴍᴀɴᴅs 𝐆ᴜɪᴅᴇ</b>\n\n"
        "• <code>/jumble</code> — 𝐒ᴛᴀʀᴛ ᴀᴜᴛᴏ-ʟᴏᴏᴘɪɴɢ ᴊᴜᴍʙʟᴇ ɢᴀᴍᴇ\n"
        "• <code>/jumblefight @user</code> — 1v1 ʙᴀᴛᴛʟᴇ ᴍᴀᴛᴄʜ\n"
        "• <code>/jumblebetfight [mode] [amount] @user</code> — 1v1 ʙᴇᴛ ᴍᴀᴛᴄʜ (75% ᴡɪɴ, 25% ᴄᴀsʜʙᴀᴄᴋ)\n"
        "• <code>/settings</code> — 𝐀ᴅᴍɪɴ sᴛᴀʀᴛ/sᴛᴏᴘ & ɢᴀᴍᴇ sᴇᴛᴛɪɴɢs\n"
        "• <code>/daily</code> — 𝐂ʟᴀɪᴍ ᴅᴀɪʟʏ ᴘᴏɪɴᴛs (𝐃𝐌 ᴏɴʟʏ)\n"
        "• <code>/bonus</code> — 𝐂ʟᴀɪᴍ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ ʀᴇᴡᴀʀᴅ (𝐆ʀᴏᴜᴘ ᴏɴʟʏ)\n"
        "• <code>/leaderboard</code> — 𝐓ᴏᴘ ᴘʟᴀʏᴇʀs ʀᴀɴᴋɪɴɢ\n"
        "• <code>/stats</code> — 𝐏ᴇʀsᴏɴᴀʟ sᴄᴏʀᴇ ᴄᴀʀᴅ\n"
        "• <code>/private</code> — 𝐇ɪᴅᴇ ᴛᴀɢ & 𝐈𝐃 ғʀᴏᴍ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ\n"
        "• <code>/public</code> — 𝐒ʜᴏᴡ ᴛᴀɢ & 𝐈𝐃 ᴏɴ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ</blockquote>"
    )
    if is_user_auth:
        text += (
            "\n\n<blockquote>🔐 <b>𝐀ᴜᴛʜ / 𝐖ᴏʀᴅ 𝐁ᴀɴᴋ 𝐂ᴏᴍᴍᴀɴᴅs:</b>\n"
            "• <code>/word</code> — 𝐕ɪᴇᴡ ᴄᴀᴛᴇɢᴏʀɪᴢᴇᴅ ᴡᴏʀᴅ ʙᴀɴᴋ\n"
            "• <code>/addword easy cat dog bird</code> — 𝐁ᴜʟᴋ ᴀᴅᴅ ᴡᴏʀᴅs\n"
            "• <code>/delword easy word</code> — 𝐃ᴇʟᴇᴛᴇ ᴡᴏʀᴅ ғʀᴏᴍ ʙᴀɴᴋ\n"
            "• <code>/delallword easy</code> — <b>𝐃ᴇʟᴇᴛᴇ ᴀʟʟ ᴡᴏʀᴅs ᴏғ ᴀ ᴍᴏᴅᴇ</b>\n"
            "• <code>/setpoints [easy|med|hard] [pts]</code> — 𝐒ᴇᴛ ɢʟᴏʙᴀʟ ᴘᴏɪɴᴛs\n"
            "• <code>/sethint [easy|med|hard] [hints]</code> — 𝐒ᴇᴛ ɢʟᴏʙᴀʟ ʜɪɴᴛs\n"
            "• <code>/setdaily [points]</code> — 𝐒ᴇᴛ ᴅᴀɪʟʏ ᴄʟᴀɪᴍ ʀᴇᴡᴀʀᴅ\n"
            "• <code>/setbonus [points]</code> — 𝐒ᴇᴛ ɢʀᴏᴜᴘ ʙᴏɴᴜs ʀᴇᴡᴀʀᴅ\n"
            "• <code>/update</code> — 𝐆ɪᴛ sᴛᴀsʜ, ᴘᴜʟʟ & 𝐀ᴜᴛᴏ-ʀᴇsᴜᴍᴇ</blockquote>"
        )
    if message.from_user and is_owner(message.from_user.id):
        text += (
            "\n\n<blockquote>👑 <b>𝐎ᴡɴᴇʀ 𝐂ᴏᴍᴍᴀɴᴅs:</b>\n"
            "• <code>/auth @user</code> — 𝐆ʀᴀɴᴛ ᴀᴜᴛʜ ᴀᴄᴄᴇss\n"
            "• <code>/unauth @user</code> — 𝐑ᴇᴠᴏᴋᴇ ᴀᴜᴛʜ ᴀᴄᴄᴇss\n"
            "• <code>/authlist</code> — 𝐋ɪsᴛ ᴏғ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ ᴜsᴇʀs</blockquote>"
        )
    await message.reply_text(text, parse_mode=ParseMode.HTML)

# ============================================================
# STATS & LEADERBOARD COMMANDS
# ============================================================

@app.on_message(filters.command(["stats", "stat", "mystats", "score"]))
async def stats_cmd(_, message: Message):
    if not message.from_user:
        return
    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    total_fights = u["fight_wins"] + u["fight_losses"]
    winrate = ((u["fight_wins"] / total_fights) * 100) if total_fights else 0
    total_bets = (u["bet_wins"] or 0) + (u["bet_losses"] or 0)
    bet_winrate = (((u["bet_wins"] or 0) / total_bets) * 100) if total_bets else 0
    mention = get_mention(message.from_user)
    priv_status = "🔒 Private" if u["is_private"] else "🌐 Public"

    await message.reply_text(
        f"<blockquote>👤 {mention} (<code>{message.from_user.id}</code>)\n\n"
        f"⭐ <b>𝐏ᴏɪɴᴛs:</b> <code>{u['points']}</code>\n"
        f"🧩 <b>𝐒ᴏʟᴠᴇᴅ:</b> <code>{u['solved']}</code>\n"
        f"🔥 <b>𝐒ᴛʀᴇᴀᴋ:</b> <code>{u['streak']}</code> (Best: {u['best_streak']})\n"
        f"🛡️ <b>𝐏ʀɪᴠᴀᴄʏ:</b> <code>{priv_status}</code>\n\n"
        f"⚔️ <b>𝐉ᴜᴍʙʟᴇ 𝐅ɪɢʜᴛ:</b> <code>{u['fight_wins']}W - {u['fight_losses']}L</code> ({winrate:.1f}%)\n"
        f"💰 <b>𝐁ᴇᴛ 𝐅ɪɢʜᴛ:</b> <code>{u['bet_wins']}W - {u['bet_losses']}L</code> ({bet_winrate:.1f}%)</blockquote>",
        parse_mode=ParseMode.HTML
    )

def format_lb_entry(user_id, name, username, is_private):
    clean_name = html.escape(str(name or "Player"))
    if is_private:
        return f"<b>{clean_name}</b>"
    
    if username:
        return f"<a href='https://t.me/{username}'>{clean_name}</a> (<code>{user_id}</code>)"
    return f"<a href='tg://openmessage?user_id={user_id}'>{clean_name}</a> (<code>{user_id}</code>)"

def build_leaderboard_text_and_kb(scope_type, chat_id):
    now = time.time()
    medals = ["🥇", "🥈", "🥉"]
    
    if scope_type == "daily":
        since = now - 86400
        title = "📅 <b>𝐃𝐀𝐈𝐋𝐘 𝐆𝐑𝐎𝐔𝐏 𝐋𝐄𝐀𝐃𝐄𝐑𝐁𝐎𝐀𝐑𝐃 (24h)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            LEFT JOIN users u ON h.user_id = u.user_id
            WHERE h.chat_id = ? AND h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (chat_id, since)).fetchall()
        
    elif scope_type == "weekly":
        since = now - (86400 * 7)
        title = "🗓️ <b>𝐖𝐄𝐄𝐊𝐋𝐘 𝐆𝐑𝐎𝐔𝐏 𝐋𝐄𝐀𝐃𝐄𝐑𝐁𝐎𝐀𝐑𝐃 (7 Days)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            LEFT JOIN users u ON h.user_id = u.user_id
            WHERE h.chat_id = ? AND h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (chat_id, since)).fetchall()

    elif scope_type == "monthly":
        since = now - (86400 * 30)
        title = "📆 <b>𝐌𝐎𝐍𝐓𝐇𝐋𝐘 𝐆𝐋𝐎𝐁𝐀𝐋 𝐋𝐄𝐀𝐃𝐄𝐑𝐁𝐎𝐀𝐑𝐃 (30 Days)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            LEFT JOIN users u ON h.user_id = u.user_id
            WHERE h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (since,)).fetchall()

    else:
        title = "🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐀𝐋𝐋-𝐓𝐈𝐌𝐄 𝐋𝐄𝐀𝐃𝐄𝐑𝐁𝐎𝐀𝐑𝐃</b>"
        rows = DB.execute("""
            SELECT user_id, name, username, is_private, points as total_pts
            FROM users
            ORDER BY points DESC
            LIMIT 10
        """).fetchall()

    text = f"<blockquote>{title}\n\n"
    if not rows:
        text += "<i>Abhi tak koi score record nahi hua hai.</i>"
    else:
        for i, u in enumerate(rows, 1):
            medal = medals[i - 1] if i <= 3 else f"<code>{i}.</code>"
            user_entry = format_lb_entry(u['user_id'], u['name'], u['username'], u['is_private'])
            text += f"{medal} {user_entry} — ⭐ <b>{u['total_pts']} pts</b>\n"
    text += "</blockquote>"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{'✅ ' if scope_type=='daily' else ''}📅 𝐃ᴀɪʟʏ (GC)", callback_data=f"lb_daily_{chat_id}"),
            InlineKeyboardButton(f"{'✅ ' if scope_type=='weekly' else ''}🗓️ 𝐖ᴇᴇᴋʟʏ (GC)", callback_data=f"lb_weekly_{chat_id}")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if scope_type=='monthly' else ''}📆 𝐌ᴏɴᴛʜʟʏ (Global)", callback_data=f"lb_monthly_{chat_id}"),
            InlineKeyboardButton(f"{'✅ ' if scope_type=='global' else ''}🌍 𝐆ʟᴏʙᴀʟ", callback_data=f"lb_global_{chat_id}")
        ],
        [
            InlineKeyboardButton("❌ 𝐂ʟᴏsᴇ", callback_data="close_panel")
        ]
    ])

    return text, kb

@app.on_message(filters.command(["leaderboard", "top", "rank", "lb"]))
async def leaderboard_cmd(_, message: Message):
    chat_id = message.chat.id
    scope = "daily" if is_group(message) else "global"
    text, kb = build_leaderboard_text_and_kb(scope, chat_id)
    await message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

# ============================================================
# SETTINGS PANEL
# ============================================================

@app.on_message(filters.command(["settings", "setting"]))
async def settings_cmd(_, message: Message):
    if not message.from_user or not await is_admin_or_owner(message.chat, message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Only group admins can configure settings.</b></blockquote>", parse_mode=ParseMode.HTML)

    s = get_settings(message.chat.id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ 𝐒ᴛᴏᴘ 𝐆ᴀᴍᴇ", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ 𝐒ᴛᴀʀᴛ 𝐆ᴀᴍᴇ", callback_data="set_start_game")
    del_btn = InlineKeyboardButton("🗑️ 𝐀ᴜᴛᴏ-𝐃ᴇʟ: 𝐎𝐍", callback_data="set_toggle_autodel") if s["auto_delete"] else InlineKeyboardButton("🗑️ 𝐀ᴜᴛᴏ-𝐃ᴇʟ: 𝐎𝐅𝐅", callback_data="set_toggle_autodel")

    p_easy = get_global_config("points_easy", 10)
    p_med = get_global_config("points_medium", 20)
    p_hard = get_global_config("points_hard", 30)

    h_easy = get_global_config("hints_easy", 3)
    h_med = get_global_config("hints_medium", 3)
    h_hard = get_global_config("hints_hard", 3)

    kb = InlineKeyboardMarkup([
        [
            status_btn,
            InlineKeyboardButton(f"🎯 𝐌ᴏᴅᴇ: {str(cur_diff).upper()}", callback_data="set_menu_mode")
        ],
        [
            InlineKeyboardButton("⏱️ 𝐓ɪᴍᴇʀs", callback_data="set_menu_timers"),
            del_btn
        ],
        [
            InlineKeyboardButton("❌ 𝐂ʟᴏsᴇ", callback_data="close_panel")
        ]
    ])
    await message.reply_text(
        f"<blockquote>⚙️ <b>𝐉ᴜᴍʙʟᴇ 𝐆ʀᴏᴜᴘ 𝐒ᴇᴛᴛɪɴɢs</b>\n\n"
        f"🟢 <b>𝐆ᴀᴍᴇ 𝐒ᴛᴀᴛᴜs:</b> <code>{'Running' if s['is_active'] else 'Stopped'}</code>\n"
        f"🗑️ <b>𝐀ᴜᴛᴏ 𝐃ᴇʟᴇᴛᴇ 𝐎ʟᴅ:</b> <code>{'Enabled' if s['auto_delete'] else 'Disabled'}</code>\n"
        f"🎯 <b>𝐃ᴇғᴀᴜʟᴛ 𝐌ᴏᴅᴇ:</b> <code>{str(cur_diff).title()}</code>\n"
        f"⏱️ <b>𝐓ɪᴍᴇʀs:</b> Easy: <code>{s['easy']}s</code> | Med: <code>{s['medium']}s</code> | Hard: <code>{s['hard']}s</code>\n\n"
        f"🌍 <b>𝐆ʟᴏʙᴀʟ 𝐑ᴇᴡᴀʀᴅs:</b> Easy: <code>{p_easy}pts</code> | Med: <code>{p_med}pts</code> | Hard: <code>{p_hard}pts</code>\n"
        f"💡 <b>𝐆ʟᴏʙᴀʟ 𝐇ɪɴᴛs:</b> Easy: <code>{h_easy}</code> | Med: <code>{h_med}</code> | Hard: <code>{h_hard}</code></blockquote>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

# ============================================================
# GLOBAL CONFIG & SETTINGS COMMANDS
# ============================================================

@app.on_message(filters.command("setpoints"))
async def set_points_global(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Sirf Owner aur Auth users global points set kar sakte hain.</b></blockquote>", parse_mode=ParseMode.HTML)

    args = message.command[1:]

    if len(args) == 1:
        try:
            pts = int(args[0])
        except ValueError:
            return await message.reply_text("❌ Invalid points number.")
        
        set_global_config("points_easy", pts)
        set_global_config("points_medium", pts)
        set_global_config("points_hard", pts)
        return await message.reply_text(f"<blockquote>🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐒𝐄𝐓𝐓𝐈𝐍𝐆 𝐔𝐏𝐃𝐀𝐓𝐄𝐃!</b>\n\nSabhi groups aur chats ke liye Easy, Medium, aur Hard reward <b>{pts} points</b> set kar diya gaya.</blockquote>", parse_mode=ParseMode.HTML)

    elif len(args) == 2:
        category = args[0].lower()
        if category not in ("easy", "medium", "hard"):
            return await message.reply_text("❌ Category must be: <code>easy</code>, <code>medium</code>, ya <code>hard</code>.", parse_mode=ParseMode.HTML)

        try:
            pts = int(args[1])
        except ValueError:
            return await message.reply_text("❌ Invalid points number.")

        set_global_config(f"points_{category}", pts)
        return await message.reply_text(f"<blockquote>🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐒𝐄𝐓𝐓𝐈𝐍𝐆 𝐔𝐏𝐃𝐀𝐓𝐄𝐃!</b>\n\nSabhi groups aur chats ke liye <b>{category.title()}</b> reward <b>{pts} points</b> set kar diya gaya.</blockquote>", parse_mode=ParseMode.HTML)

    else:
        return await message.reply_text(
            "<blockquote><b>Global Usage:</b>\n"
            "• <code>/setpoints 20</code> — Sabhi categories ke liye globally\n"
            "• <code>/setpoints easy 10</code> — Sirf Easy ke liye globally\n"
            "• <code>/setpoints medium 25</code> — Sirf Medium ke liye globally\n"
            "• <code>/setpoints hard 50</code> — Sirf Hard ke liye globally</blockquote>",
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.command("sethint"))
async def sethint_global(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Sirf Owner aur Auth users global hints set kar sakte hain.</b></blockquote>", parse_mode=ParseMode.HTML)

    args = message.command[1:]

    if len(args) == 1:
        try:
            h = int(args[0])
        except ValueError:
            return await message.reply_text("❌ Invalid hint number.")

        set_global_config("hints_easy", h)
        set_global_config("hints_medium", h)
        set_global_config("hints_hard", h)
        return await message.reply_text(f"<blockquote>🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐒𝐄𝐓𝐓𝐈𝐍𝐆 𝐔𝐏𝐃𝐀𝐓𝐄𝐃!</b>\n\nSabhi groups aur chats ke liye hints limit <b>{h} hints/word</b> set kar di gayi.</blockquote>", parse_mode=ParseMode.HTML)

    elif len(args) == 2:
        category = args[0].lower()
        if category not in ("easy", "medium", "hard"):
            return await message.reply_text("❌ Category must be: <code>easy</code>, <code>medium</code>, ya <code>hard</code>.", parse_mode=ParseMode.HTML)

        try:
            h = int(args[1])
        except ValueError:
            return await message.reply_text("❌ Invalid hint number.")

        set_global_config(f"hints_{category}", h)
        return await message.reply_text(f"<blockquote>🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐒𝐄𝐓𝐓𝐈𝐍𝐆 𝐔𝐏𝐃𝐀𝐓𝐄𝐃!</b>\n\nSabhi groups aur chats ke liye <b>{category.title()}</b> hints limit <b>{h} hints/word</b> set kar di gayi.</blockquote>", parse_mode=ParseMode.HTML)

    else:
        return await message.reply_text(
            "<blockquote><b>Global Usage:</b>\n"
            "• <code>/sethint 3</code> — Sabhi categories ke liye globally\n"
            "• <code>/sethint easy 5</code> — Sirf Easy ke liye globally\n"
            "• <code>/sethint medium 3</code> — Sirf Medium ke liye globally\n"
            "• <code>/sethint hard 2</code> — Sirf Hard ke liye globally</blockquote>",
            parse_mode=ParseMode.HTML
        )

# ============================================================
# DAILY & STRICT GROUP BONUS CLAIM SYSTEM
# ============================================================

@app.on_message(filters.command("setdaily"))
async def set_daily_cmd(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users daily reward set kar sakte hain.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/setdaily 100</code>", parse_mode=ParseMode.HTML)

    try:
        val = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid number.")

    set_global_config("daily_points", val)
    await message.reply_text(f"<blockquote>✅ <b>Daily claim reward <b>{val} points</b> set kar diya gaya.</b></blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("setbonus"))
async def set_bonus_cmd(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users group bonus reward set kar sakte hain.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/setbonus 200</code>", parse_mode=ParseMode.HTML)

    try:
        val = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid number.")

    set_global_config("bonus_points", val)
    await message.reply_text(f"<blockquote>✅ <b>Group admin bonus reward <b>{val} points</b> set kar diya gaya.</b></blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("daily"))
async def daily_cmd(_, message: Message):
    if not message.from_user:
        return
    if message.chat.type != ChatType.PRIVATE:
        return await message.reply_text("<blockquote>❌ <b><code>/daily</code> command sirf bot ke DM (Private Chat) mein use kar sakte hain.</b></blockquote>", parse_mode=ParseMode.HTML)

    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    now = time.time()
    last = u["last_daily"] or 0
    cooldown = 86400

    if now - last < cooldown:
        rem = int(cooldown - (now - last))
        hrs = rem // 3600
        mins = (rem % 3600) // 60
        return await message.reply_text(f"<blockquote>⏳ <b>Aapne aaj ka daily reward claim kar liya hai!</b>\nNext claim available in: <b>{hrs}h {mins}m</b></blockquote>", parse_mode=ParseMode.HTML)

    reward = get_global_config("daily_points", 50)
    DB.execute("""
        UPDATE users 
        SET points = points + ?, last_daily = ?
        WHERE user_id = ?
    """, (reward, now, message.from_user.id))
    
    DB.execute("""
        INSERT INTO score_history (user_id, chat_id, points, timestamp)
        VALUES (?, 0, ?, ?)
    """, (message.from_user.id, reward, now))
    DB.commit()

    await message.reply_text(
        f"<blockquote>🎁 <b>𝐃ᴀɪʟʏ 𝐑ᴇᴡᴀʀᴅ 𝐂ʟᴀɪᴍᴇᴅ!</b>\n\n"
        f"⭐ <b>+{reward} Points</b> successfully aapke balance mein add kar diye gaye hain.\n"
        f"Wapas 24 ghante baad claim karein!</blockquote>",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("bonus"))
async def bonus_cmd(_, message: Message):
    if not message.from_user:
        return
    if not is_group(message):
        return await message.reply_text("<blockquote>❌ <b><code>/bonus</code> command sirf group mein chal sakti hai jahan aapne bot ko add karke admin banaya hai.</b></blockquote>", parse_mode=ParseMode.HTML)

    ensure_user(message.from_user)
    chat_id = message.chat.id
    user_id = message.from_user.id

    promoted_by_user_id = None
    try:
        bot_member = await message.chat.get_member("me")
        if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return await message.reply_text("<blockquote>⚠️ <b>Bonus claim karne ke liye pehle bot ko is group mein Admin Rights dein!</b></blockquote>", parse_mode=ParseMode.HTML)
        
        if getattr(bot_member, "promoted_by", None):
            promoted_by_user_id = bot_member.promoted_by.id
    except Exception:
        return await message.reply_text("<blockquote>❌ <b>Bot ke admin permissions verify nahi ho sake. Kripya bot ko Admin banayein.</b></blockquote>", parse_mode=ParseMode.HTML)

    claimed = DB.execute("SELECT * FROM group_bonus WHERE chat_id=?", (chat_id,)).fetchone()
    if claimed:
        return await message.reply_text("<blockquote>⚠️ <b>Is group ka bonus already claim kiya ja chuka hai!</b></blockquote>", parse_mode=ParseMode.HTML)

    adder_row = DB.execute("SELECT user_id FROM group_adders WHERE chat_id=?", (chat_id,)).fetchone()
    valid_claimant_id = adder_row["user_id"] if adder_row else promoted_by_user_id

    if not valid_claimant_id:
        try:
            member = await message.chat.get_member(user_id)
            if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                valid_claimant_id = user_id
                DB.execute("""
                    INSERT INTO group_adders (chat_id, user_id, added_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET user_id=excluded.user_id
                """, (chat_id, user_id, time.time()))
                DB.commit()
        except Exception:
            pass

    if valid_claimant_id and user_id != valid_claimant_id and not is_owner(user_id):
        return await message.reply_text("<blockquote>❌ <b>Yeh bonus sirf wahi user claim kar sakta hai jisne bot ko is group mein add ya admin banaya hai!</b></blockquote>", parse_mode=ParseMode.HTML)

    bonus_pts = get_global_config("bonus_points", 100)
    now = time.time()

    DB.execute("""
        INSERT INTO group_bonus (chat_id, user_id, claimed_at)
        VALUES (?, ?, ?)
    """, (chat_id, user_id, now))

    DB.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (bonus_pts, user_id))
    DB.execute("""
        INSERT INTO score_history (user_id, chat_id, points, timestamp)
        VALUES (?, ?, ?, ?)
    """, (user_id, chat_id, bonus_pts, now))
    DB.commit()

    mention = get_mention(message.from_user)
    await message.reply_text(
        f"<blockquote>🎉 <b>𝐆ʀᴏᴜᴘ 𝐁ᴏɴᴜs 𝐂ʟᴀɪᴍᴇᴅ!</b>\n\n"
        f"👤 {mention}\n"
        f"⭐ <b>+{bonus_pts} Points</b> successfully aapke profile mein add ho gaye hain bot ko add karke admin banane ke reward ke roop mein!</blockquote>",
        parse_mode=ParseMode.HTML
    )

# ============================================================
# PRIVACY SYSTEM (/private & /public)
# ============================================================

@app.on_message(filters.command("private"))
async def private_cmd(_, message: Message):
    if not message.from_user:
        return
    ensure_user(message.from_user)
    DB.execute("UPDATE users SET is_private=1 WHERE user_id=?", (message.from_user.id,))
    DB.commit()

    await message.reply_text(
        "<blockquote>🔒 <b>𝐏ʀɪᴠᴀᴄʏ 𝐄ɴᴀʙʟᴇᴅ!</b>\n\n"
        "Leaderboard par aapka <b>Tag, Link aur User ID hide</b> kar diya gaya hai. Sirf aapka plain name dikhega.\n"
        "Wapas tag show karne ke liye <code>/public</code> use karein.</blockquote>",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("public"))
async def public_cmd(_, message: Message):
    if not message.from_user:
        return
    ensure_user(message.from_user)
    DB.execute("UPDATE users SET is_private=0 WHERE user_id=?", (message.from_user.id,))
    DB.commit()

    await message.reply_text(
        "<blockquote>🌐 <b>𝐏ᴜʙʟɪᴄ 𝐌ᴏᴅᴇ 𝐄ɴᴀʙʟᴇᴅ!</b>\n\n"
        "Leaderboard par aapka <b>Username, Tag Link aur User ID</b> display hoga.\n"
        "Hide karne ke liye <code>/private</code> use karein.</blockquote>",
        parse_mode=ParseMode.HTML
    )

# ============================================================
# GIT UPDATER (AUTH / OWNER ONLY)
# ============================================================

@app.on_message(filters.command(["update", "gitpull"]))
async def update_bot_cmd(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Authorized users bot update kar sakte hain.")

    msg = await message.reply_text("<blockquote>🔄 <b>Pulling latest changes from GitHub...</b></blockquote>", parse_mode=ParseMode.HTML)
    try:
        subprocess.run(["git", "stash"], check=True, capture_output=True, text=True)
        pull_res = subprocess.run(["git", "pull"], check=True, capture_output=True, text=True)
        out = pull_res.stdout or "Updated successfully."
        
        await msg.edit_text(f"<blockquote>✅ <b>Git Pull Output:</b>\n<code>{out[:500]}</code>\n\n🚀 <b>Restarting & Auto-resuming all active group games...</b></blockquote>", parse_mode=ParseMode.HTML)
        await asyncio.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit_text(f"<blockquote>❌ <b>Update Failed:</b>\n<code>{str(e)}</code></blockquote>", parse_mode=ParseMode.HTML)

# ============================================================
# AUTH SYSTEM (AUTO-CLEANUP COMMANDS)
# ============================================================

@app.on_message(filters.command("auth"))
async def auth_cmd(_, message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf Bot Owner auth de sakta hai.")

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        try:
            arg = message.command[1]
            target = await app.get_users(int(arg) if arg.isdigit() else arg)
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    else:
        return await message.reply_text("Usage:\n<code>/auth @username</code> or Reply <code>/auth</code>", parse_mode=ParseMode.HTML)

    if target.is_bot:
        return await message.reply_text("❌ Bots ko auth nahi diya ja sakta.")

    DB.execute("""
        INSERT INTO auth_users(user_id, username, name, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            name=excluded.name
    """, (target.id, target.username or "", target.first_name or "User", time.time()))
    DB.commit()

    mention = get_mention(target)
    res = await message.reply_text(f"<blockquote>✅ {mention} (<code>{target.id}</code>) ko <b>Auth Access</b> de diya gaya.</blockquote>", parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("unauth"))
async def unauth_cmd(_, message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf Bot Owner unauth kar sakta hai.")

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        try:
            arg = message.command[1]
            target = await app.get_users(int(arg) if arg.isdigit() else arg)
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    else:
        return await message.reply_text("Usage:\n<code>/unauth @username</code> or Reply <code>/unauth</code>", parse_mode=ParseMode.HTML)

    DB.execute("DELETE FROM auth_users WHERE user_id=?", (target.id,))
    DB.commit()

    mention = get_mention(target)
    res = await message.reply_text(f"<blockquote>🚫 {mention} (<code>{target.id}</code>) ka auth access remove kar diya gaya.</blockquote>", parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("authlist"))
async def authlist_cmd(_, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users authlist dekh sakte hain.")

    rows = DB.execute("SELECT * FROM auth_users ORDER BY added_at DESC").fetchall()
    text = "<blockquote>🔐 <b>𝐀𝐔𝐓𝐇𝐎𝐑𝐈𝐙𝐄𝐃 𝐔𝐒𝐄𝐑𝐒 𝐋𝐈𝐒𝐓</b>\n\n"
    text += f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n\n"

    if not rows:
        text += "Koi extra authorized user nahi hai."
    else:
        for i, row in enumerate(rows, 1):
            m = get_mention(user_id=row['user_id'], first_name=row['name'], username=row['username'])
            text += f"<code>{i}.</code> {m} — ID: <code>{row['user_id']}</code>\n"
    text += "</blockquote>"

    res = await message.reply_text(text, parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 10))
    asyncio.create_task(delete_after(res, 10))

# ============================================================
# AUTO-RESUME GAMES ON BOT STARTUP
# ============================================================

async def resume_all_active_games():
    await asyncio.sleep(3)
    rows = DB.execute("SELECT chat_id, default_diff FROM settings WHERE is_active = 1 AND chat_id != 0").fetchall()
    
    for row in rows:
        c_id = row["chat_id"]
        diff = row["default_diff"] or "medium"
        try:
            DB.execute("DELETE FROM games WHERE chat_id=?", (c_id,))
            DB.commit()
            
            await start_game(c_id, diff, c_id)
            await asyncio.sleep(0.8)
        except Exception as e:
            print(f"Error auto-resuming game in {c_id}: {e}")

# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    print("🚀 Advanced Jumble & Jumble Fight Bot Started Successfully!")
    app.start()
    asyncio.get_event_loop().create_task(resume_all_active_games())
    from pyrogram import idle
    idle()
    app.stop()
