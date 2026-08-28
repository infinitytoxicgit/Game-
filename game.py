import asyncio
import io
import os
import random
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus, ParseMode
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

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

CREATE TABLE IF NOT EXISTS group_bonus (
    user_id INTEGER,
    chat_id INTEGER,
    claimed_at REAL,
    PRIMARY KEY(user_id, chat_id)
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
    # Global Config Defaults
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
    if "is_private" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN is_private INTEGER DEFAULT 0")
    if "last_daily" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN last_daily REAL DEFAULT 0")

    DB.commit()

run_migrations()

WORDS = {
    "easy": list(set(w.lower() for w in DEFAULT_EASY if len(w) >= 4)),
    "medium": list(set(w.lower() for w in DEFAULT_MEDIUM if len(w) >= 6)),
    "hard": list(set(w.lower() for w in DEFAULT_HARD if len(w) >= 8))
}

custom_rows = DB.execute("SELECT difficulty, word FROM custom_words").fetchall()
for row in custom_rows:
    diff = row["difficulty"]
    w = row["word"].lower()
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
    return int(user_id) == int(OWNER_ID)

def is_authed(user_id):
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
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) or str(message.chat.type).lower() in ("group", "supergroup", "chattype.group", "chattype.supergroup")

def get_mention(user_obj=None, user_id=None, first_name=None, username=None):
    if user_obj:
        u_id = user_obj.id
        f_name = user_obj.first_name or "Player"
        u_name = user_obj.username
    else:
        u_id = user_id
        f_name = first_name or "Player"
        u_name = username

    clean_name = f_name.replace("<", "&lt;").replace(">", "&gt;")
    if u_name:
        return f"<a href='https://t.me/{u_name}'>{clean_name}</a>"
    return f"<a href='tg://openmessage?user_id={u_id}'>{clean_name}</a>"

def clean_answer(text):
    return "".join(c.lower() for c in text if c.isalnum())

def jumble_word(word):
    letters = list(word)
    for _ in range(50):
        random.shuffle(letters)
        result = "".join(letters)
        if result != word and result[::-1] != word:
            return result.upper()
    return "".join(letters).upper()

def choose_word(chat_id, difficulty):
    pool = WORDS[difficulty][:]
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
# NORMAL GAME CORE
# ============================================================

def normal_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 Hint", callback_data="hint"),
            InlineKeyboardButton("⏭️ Skip", callback_data="skip")
        ],
        [
            InlineKeyboardButton("🆕 New Word", callback_data="newword")
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
        f"🧩 <b>Jumble #{puzzle_id}</b>\n\n"
        f"🎯 Difficulty: <b>{difficulty.title()}</b>\n"
        f"⏱️ Time: <b>{timer_val // 60} min {timer_val % 60} sec</b>\n"
        f"⭐ Reward: <b>+{reward_pts} Points</b> | 💡 Hints: <b>{hint_limit}/word</b>\n\n"
        f"🔀 Unscramble the letters!\n"
        f"💬 Type your answer in the chat."
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
        print(f"Error sending puzzle: {e}")

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
            f"⏰ <b>Time's Up!</b>\n\n❌ Nobody solved it.\n✅ Answer: <b>{row['word'].upper()}</b>\n\n🔄 Next puzzle starting in 3 seconds...",
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
# JUMBLE FIGHT (1v1 BATTLE SYSTEM)
# ============================================================

JUMBLE_FIGHT = {}
FIGHT_LOBBY = {}

def fight_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 Hint", callback_data="fight_hint")
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
                    f"⏰ <b>Round {round_num} Timeout!</b>\n❌ Kisi ne solve nahi kiya.\n✅ Answer: <b>{word.upper()}</b>\n\n🔄 Next round starting...",
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

    image = make_puzzle_image(jumbled, f"FIGHT {diff.upper()}", game["round"])
    
    try:
        sent = await app.send_photo(
            chat_id,
            photo=image,
            caption=(
                f"⚔️ <b>JUMBLE FIGHT — ROUND {game['round']}/10</b>\n\n"
                f"🎯 Difficulty: <b>{diff.title()}</b>\n"
                f"⏱️ Time: <b>{game['timer']}s</b>\n"
                f"🔀 Solve fastest!\n"
                f"👥 Players: {game['mentions'][game['players'][0]]} 🆚 {game['mentions'][game['players'][1]]}"
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

    if s1 > s2:
        winner, loser = p1, p2
    elif s2 > s1:
        winner, loser = p2, p1
    else:
        winner = loser = None

    if winner:
        DB.execute("UPDATE users SET fight_wins=fight_wins+1 WHERE user_id=?", (winner,))
        DB.execute("UPDATE users SET fight_losses=fight_losses+1 WHERE user_id=?", (loser,))
        DB.commit()

    m1 = game["mentions"][p1]
    m2 = game["mentions"][p2]
    result = f"🏁 <b>JUMBLE FIGHT OVER!</b>\n\n👤 {m1} — <b>{s1} pts</b>\n👤 {m2} — <b>{s2} pts</b>\n\n"

    if winner:
        result += f"🏆 Match Winner: {game['mentions'][winner]} 🎉"
    else:
        result += "🤝 <b>Match Draw!</b>"

    await app.send_message(chat_id, result, parse_mode=ParseMode.HTML)

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if s["is_active"]:
        await app.send_message(chat_id, "🔄 Resuming normal Jumble Game...", parse_mode=ParseMode.HTML)
        asyncio.create_task(start_game(chat_id, s["default_diff"], chat_id))

# ============================================================
# COMMAND HANDLERS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    ensure_user(message.from_user)
    text = (
        "🧩 <b>Welcome to Advanced Jumble Bot!</b>\n\n"
        "🎮 <b>Game Commands:</b>\n"
        "• <code>/jumble</code> — Start Auto-loop Jumble Game\n"
        "• <code>/jumblefight @user</code> — 1v1 Battle Mode (with Accept Gate)\n"
        "• <code>/settings</code> — Admin Panel (Start/Stop, Mode, Auto-delete)\n\n"
        "🎁 <b>Free Points & Rewards:</b>\n"
        "• <code>/daily</code> — Claim daily bonus points in DM (every 24h)\n"
        "• <code>/bonus</code> — Claim group addition bonus (when bot is added as admin)\n\n"
        "🛡️ <b>Privacy Settings:</b>\n"
        "• <code>/private</code> — Hide ID/Tag on Leaderboard (Name only)\n"
        "• <code>/public</code> — Show Tag & ID on Leaderboard\n\n"
        "📊 <b>Stats & Rankings:</b>\n"
        "• <code>/stats</code> — Your Performance\n"
        "• <code>/leaderboard</code> — Daily, Weekly, Monthly & Global Ranks\n"
        "• <code>/help</code> — Full Bot Guide"
    )

    dm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Support Group", url=SUPPORT_GC)]
    ])

    if message.chat.type in (ChatType.PRIVATE,):
        try:
            await message.reply_photo(photo=START_IMG, caption=text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    is_user_auth = is_authed(message.from_user.id)
    text = (
        "🧩 <b>Jumble Commands Guide</b>\n\n"
        "<code>/jumble</code> — Start auto-looping jumble game\n"
        "<code>/jumblefight @user</code> — 1v1 battle match\n"
        "<code>/settings</code> — Admin start/stop and game settings\n"
        "<code>/daily</code> — Claim daily points (DM only)\n"
        "<code>/bonus</code> — Claim group admin reward (Group only)\n"
        "<code>/leaderboard</code> — Top players ranking (Daily/Weekly/Monthly/Global)\n"
        "<code>/stats</code> — Personal score card\n"
        "<code>/private</code> — Hide tag & ID from leaderboard\n"
        "<code>/public</code> — Show tag & ID on leaderboard\n\n"
    )
    if is_user_auth:
        text += (
            "🔐 <b>Auth / Word Bank Commands (Global Effect):</b>\n"
            "• <code>/word</code> — View categorized word bank (with Pagination)\n"
            "• <code>/word easy cat dog bird tree</code> — Bulk add words directly\n"
            "• <code>/addword easy apple banana</code> — Add single/multiple words\n"
            "• <code>/delword easy word</code> — Delete word from bank\n"
            "• <code>/setpoints [easy|med|hard] [pts]</code> — Set GLOBAL points for all chats\n"
            "• <code>/sethint [easy|med|hard] [hints]</code> — Set GLOBAL hints for all chats\n"
            "• <code>/setdaily [points]</code> — Set daily claim reward\n"
            "• <code>/setbonus [points]</code> — Set group bonus reward\n"
            "• <code>/update</code> — Git stash, pull & Auto-resume all groups\n"
        )
    if is_owner(message.from_user.id):
        text += (
            "\n👑 <b>Owner Commands:</b>\n"
            "• <code>/auth @user</code> — Grant auth access\n"
            "• <code>/unauth @user</code> — Revoke auth access\n"
            "• <code>/authlist</code> — List of authorized users\n"
        )
    await message.reply_text(text, parse_mode=ParseMode.HTML)

# ============================================================
# GLOBAL SETTINGS (SETPOINTS & SETHINTS GLOBALLY)
# ============================================================

@app.on_message(filters.command("setpoints"))
async def set_points_global(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users global points set kar sakte hain.")

    args = message.command[1:]

    if len(args) == 1:
        try:
            pts = int(args[0])
        except ValueError:
            return await message.reply_text("❌ Invalid points number.")
        
        set_global_config("points_easy", pts)
        set_global_config("points_medium", pts)
        set_global_config("points_hard", pts)
        return await message.reply_text(f"🌍 <b>GLOBAL SETTING UPDATED!</b>\n\nSabhi groups aur chats ke liye Easy, Medium, aur Hard reward <b>{pts} points</b> set kar diya gaya.", parse_mode=ParseMode.HTML)

    elif len(args) == 2:
        category = args[0].lower()
        if category not in ("easy", "medium", "hard"):
            return await message.reply_text("❌ Category must be: <code>easy</code>, <code>medium</code>, ya <code>hard</code>.", parse_mode=ParseMode.HTML)

        try:
            pts = int(args[1])
        except ValueError:
            return await message.reply_text("❌ Invalid points number.")

        set_global_config(f"points_{category}", pts)
        return await message.reply_text(f"🌍 <b>GLOBAL SETTING UPDATED!</b>\n\nSabhi groups aur chats ke liye <b>{category.title()}</b> reward <b>{pts} points</b> set kar diya gaya.", parse_mode=ParseMode.HTML)

    else:
        return await message.reply_text(
            "<b>Global Usage:</b>\n"
            "• <code>/setpoints 20</code> — Sabhi categories ke liye globally\n"
            "• <code>/setpoints easy 10</code> — Sirf Easy ke liye globally\n"
            "• <code>/setpoints medium 25</code> — Sirf Medium ke liye globally\n"
            "• <code>/setpoints hard 50</code> — Sirf Hard ke liye globally",
            parse_mode=ParseMode.HTML
        )

@app.on_message(filters.command("sethint"))
async def sethint_global(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users global hints set kar sakte hain.")

    args = message.command[1:]

    if len(args) == 1:
        try:
            h = int(args[0])
        except ValueError:
            return await message.reply_text("❌ Invalid hint number.")

        set_global_config("hints_easy", h)
        set_global_config("hints_medium", h)
        set_global_config("hints_hard", h)
        return await message.reply_text(f"🌍 <b>GLOBAL SETTING UPDATED!</b>\n\nSabhi groups aur chats ke liye hints limit <b>{h} hints/word</b> set kar di gayi.", parse_mode=ParseMode.HTML)

    elif len(args) == 2:
        category = args[0].lower()
        if category not in ("easy", "medium", "hard"):
            return await message.reply_text("❌ Category must be: <code>easy</code>, <code>medium</code>, ya <code>hard</code>.", parse_mode=ParseMode.HTML)

        try:
            h = int(args[1])
        except ValueError:
            return await message.reply_text("❌ Invalid hint number.")

        set_global_config(f"hints_{category}", h)
        return await message.reply_text(f"🌍 <b>GLOBAL SETTING UPDATED!</b>\n\nSabhi groups aur chats ke liye <b>{category.title()}</b> hints limit <b>{h} hints/word</b> set kar di gayi.", parse_mode=ParseMode.HTML)

    else:
        return await message.reply_text(
            "<b>Global Usage:</b>\n"
            "• <code>/sethint 3</code> — Sabhi categories ke liye globally\n"
            "• <code>/sethint easy 5</code> — Sirf Easy ke liye globally\n"
            "• <code>/sethint medium 3</code> — Sirf Medium ke liye globally\n"
            "• <code>/sethint hard 2</code> — Sirf Hard ke liye globally",
            parse_mode=ParseMode.HTML
        )

# ============================================================
# DAILY & GROUP BONUS CLAIM SYSTEM
# ============================================================

@app.on_message(filters.command("setdaily"))
async def set_daily_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users daily reward set kar sakte hain.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/setdaily 100</code>", parse_mode=ParseMode.HTML)

    try:
        val = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid number.")

    set_global_config("daily_points", val)
    await message.reply_text(f"✅ Daily claim reward <b>{val} points</b> set kar diya gaya.", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("setbonus"))
async def set_bonus_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users group bonus reward set kar sakte hain.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/setbonus 200</code>", parse_mode=ParseMode.HTML)

    try:
        val = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid number.")

    set_global_config("bonus_points", val)
    await message.reply_text(f"✅ Group admin bonus reward <b>{val} points</b> set kar diya gaya.", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("daily"))
async def daily_cmd(_, message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return await message.reply_text("❌ <code>/daily</code> command sirf bot ke DM (Private Chat) mein use kar sakte hain.", parse_mode=ParseMode.HTML)

    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    now = time.time()
    last = u["last_daily"] or 0
    cooldown = 86400

    if now - last < cooldown:
        rem = int(cooldown - (now - last))
        hrs = rem // 3600
        mins = (rem % 3600) // 60
        return await message.reply_text(f"⏳ Aapne aaj ka daily reward claim kar liya hai!\nNext claim available in: <b>{hrs}h {mins}m</b>", parse_mode=ParseMode.HTML)

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
        f"🎁 <b>Daily Reward Claimed!</b>\n\n"
        f"⭐ <b>+{reward} Points</b> successfully aapke balance mein add kar diye gaye hain.\n"
        f"Wapas 24 ghante baad claim karein!",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("bonus"))
async def bonus_cmd(_, message: Message):
    if not is_group(message):
        return await message.reply_text("❌ <code>/bonus</code> command sirf group mein chal sakti hai jahan aapne bot ko admin banaya hai.", parse_mode=ParseMode.HTML)

    ensure_user(message.from_user)
    chat_id = message.chat.id
    user_id = message.from_user.id

    try:
        bot_member = await message.chat.get_member("me")
        if bot_member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return await message.reply_text("⚠️ Bonus claim karne ke liye pehle bot ko is group mein <b>Admin Rights</b> dein!", parse_mode=ParseMode.HTML)
    except Exception:
        return await message.reply_text("❌ Bot ke permissions verify nahi ho sake. Kripya bot ko Admin banayein.")

    claimed = DB.execute("SELECT * FROM group_bonus WHERE user_id=? AND chat_id=?", (user_id, chat_id)).fetchone()
    if claimed:
        return await message.reply_text("⚠️ Aapne is group ke liye bonus pehle hi claim kar liya hai!")

    bonus_pts = get_global_config("bonus_points", 100)
    now = time.time()

    DB.execute("""
        INSERT INTO group_bonus (user_id, chat_id, claimed_at)
        VALUES (?, ?, ?)
    """, (user_id, chat_id, now))

    DB.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (bonus_pts, user_id))
    DB.execute("""
        INSERT INTO score_history (user_id, chat_id, points, timestamp)
        VALUES (?, ?, ?, ?)
    """, (user_id, chat_id, bonus_pts, now))
    DB.commit()

    mention = get_mention(message.from_user)
    await message.reply_text(
        f"🎉 <b>Group Bonus Claimed!</b>\n\n"
        f"👤 {mention}\n"
        f"⭐ <b>+{bonus_pts} Points</b> successfully aapke profile mein add ho gaye hain bot ko admin banane ke reward ke roop mein!",
        parse_mode=ParseMode.HTML
    )

# ============================================================
# PRIVACY SYSTEM (/private & /public)
# ============================================================

@app.on_message(filters.command("private"))
async def private_cmd(_, message: Message):
    ensure_user(message.from_user)
    DB.execute("UPDATE users SET is_private=1 WHERE user_id=?", (message.from_user.id,))
    DB.commit()

    await message.reply_text(
        "🔒 <b>Privacy Enabled!</b>\n\n"
        "Leaderboard par aapka <b>Tag, Link aur User ID hide</b> kar diya gaya hai. Sirf aapka plain name dikhega.\n"
        "Wapas tag show karne ke liye <code>/public</code> use karein.",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("public"))
async def public_cmd(_, message: Message):
    ensure_user(message.from_user)
    DB.execute("UPDATE users SET is_private=0 WHERE user_id=?", (message.from_user.id,))
    DB.commit()

    await message.reply_text(
        "🌐 <b>Public Mode Enabled!</b>\n\n"
        "Leaderboard par aapka <b>Username, Tag Link aur User ID</b> display hoga.\n"
        "Hide karne ke liye <code>/private</code> use karein.",
        parse_mode=ParseMode.HTML
    )

# ============================================================
# GIT UPDATER (AUTH / OWNER ONLY)
# ============================================================

@app.on_message(filters.command(["update", "gitpull"]))
async def update_bot_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Authorized users bot update kar sakte hain.")

    msg = await message.reply_text("🔄 <b>Pulling latest changes from GitHub...</b>", parse_mode=ParseMode.HTML)
    try:
        subprocess.run(["git", "stash"], check=True, capture_output=True, text=True)
        pull_res = subprocess.run(["git", "pull"], check=True, capture_output=True, text=True)
        out = pull_res.stdout or "Updated successfully."
        
        await msg.edit_text(f"✅ <b>Git Pull Output:</b>\n<code>{out[:500]}</code>\n\n🚀 <b>Restarting & Auto-resuming all active group games...</b>", parse_mode=ParseMode.HTML)
        await asyncio.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit_text(f"❌ <b>Update Failed:</b>\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)

# ============================================================
# AUTH SYSTEM (AUTO-CLEANUP COMMANDS)
# ============================================================

@app.on_message(filters.command("auth"))
async def auth_cmd(_, message: Message):
    if not is_owner(message.from_user.id):
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
    res = await message.reply_text(f"✅ {mention} (<code>{target.id}</code>) ko <b>Auth Access</b> de diya gaya.", parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("unauth"))
async def unauth_cmd(_, message: Message):
    if not is_owner(message.from_user.id):
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
    res = await message.reply_text(f"🚫 {mention} (<code>{target.id}</code>) ka auth access remove kar diya gaya.", parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("authlist"))
async def authlist_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users authlist dekh sakte hain.")

    rows = DB.execute("SELECT * FROM auth_users ORDER BY added_at DESC").fetchall()
    text = "🔐 <b>AUTHORIZED USERS LIST</b>\n\n"
    text += f"👑 <b>Owner:</b> <code>{OWNER_ID}</code>\n\n"

    if not rows:
        text += "Koi extra authorized user nahi hai."
    else:
        for i, row in enumerate(rows, 1):
            m = get_mention(user_id=row['user_id'], first_name=row['name'], username=row['username'])
            text += f"<code>{i}.</code> {m} — ID: <code>{row['user_id']}</code>\n"

    res = await message.reply_text(text, parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 10))
    asyncio.create_task(delete_after(res, 10))

# ============================================================
# WORD BANK MANAGEMENT (SINGLE / BULK ADDITION SUPPORT)
# ============================================================

def handle_bulk_add_words(difficulty, raw_words_list):
    added = []
    skipped = []
    for raw in raw_words_list:
        w = clean_answer(raw)
        if len(w) >= 3:
            if w not in WORDS[difficulty]:
                WORDS[difficulty].append(w)
                DB.execute("INSERT OR IGNORE INTO custom_words(difficulty, word) VALUES (?, ?)", (difficulty, w))
                added.append(w)
            else:
                skipped.append(w)
    DB.commit()
    return added, skipped

@app.on_message(filters.command("addword"))
async def addword_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

    if len(message.command) < 3:
        return await message.reply_text("Usage:\n<code>/addword easy apple banana orange</code>\n<code>/addword medium computer network</code>\n<code>/addword hard international transformation</code>", parse_mode=ParseMode.HTML)

    difficulty = message.command[1].lower()
    if difficulty not in WORDS:
        return await message.reply_text("❌ Valid difficulties: <code>easy</code>, <code>medium</code>, <code>hard</code>.", parse_mode=ParseMode.HTML)

    raw_words = message.text.split(None, 2)[2].replace(",", " ").split()
    added, skipped = handle_bulk_add_words(difficulty, raw_words)

    msg_text = f"✅ <b>{len(added)}</b> word(s) successfully added to <b>{difficulty.upper()}</b> bank!"
    if skipped:
        msg_text += f"\n⚠️ <i>{len(skipped)} word(s) already exist karte the (Skipped).</i>"

    res = await message.reply_text(msg_text, parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("delword"))
async def delword_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

    if len(message.command) < 3:
        return await message.reply_text("Usage:\n<code>/delword easy apple</code>\n<code>/delword medium computer</code>\n<code>/delword hard international</code>", parse_mode=ParseMode.HTML)

    difficulty = message.command[1].lower()
    word_to_del = clean_answer(message.command[2])

    if difficulty not in WORDS:
        return await message.reply_text("❌ Valid difficulties: <code>easy</code>, <code>medium</code>, <code>hard</code>.", parse_mode=ParseMode.HTML)

    if word_to_del not in WORDS[difficulty]:
        res = await message.reply_text(f"❌ Word <b>'{word_to_del.upper()}'</b> {difficulty.upper()} bank mein nahi mila.", parse_mode=ParseMode.HTML)
        asyncio.create_task(delete_after(message, 5))
        asyncio.create_task(delete_after(res, 5))
        return

    WORDS[difficulty].remove(word_to_del)
    DB.execute("DELETE FROM custom_words WHERE difficulty=? AND word=?", (difficulty, word_to_del))
    DB.execute("DELETE FROM used_words WHERE difficulty=? AND word=?", (difficulty, word_to_del))
    DB.commit()

    res = await message.reply_text(f"🗑️ Word <b>'{word_to_del.upper()}'</b> deleted from <b>{difficulty.upper()}</b> bank!", parse_mode=ParseMode.HTML)
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command(["word", "words"]))
async def words_menu_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

    if len(message.command) >= 3 and message.command[1].lower() in WORDS:
        diff = message.command[1].lower()
        raw_words = message.text.split(None, 2)[2].replace(",", " ").split()
        added, skipped = handle_bulk_add_words(diff, raw_words)
        
        msg_text = f"✅ <b>{len(added)}</b> word(s) successfully added to <b>{diff.upper()}</b> bank!"
        if skipped:
            msg_text += f"\n⚠️ <i>{len(skipped)} word(s) already exist karte the (Skipped).</i>"

        res = await message.reply_text(msg_text, parse_mode=ParseMode.HTML)
        asyncio.create_task(delete_after(message, 5))
        asyncio.create_task(delete_after(res, 5))
        return

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🟢 Easy ({len(WORDS['easy'])})", callback_data="wb_easy_1"),
            InlineKeyboardButton(f"🟡 Medium ({len(WORDS['medium'])})", callback_data="wb_medium_1"),
            InlineKeyboardButton(f"🔴 Hard ({len(WORDS['hard'])})", callback_data="wb_hard_1")
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
        ]
    ])

    await message.reply_text(
        "📚 <b>JUMBLE WORD BANK</b>\n\n"
        f"🟢 <b>Easy Words:</b> <code>{len(WORDS['easy'])}</code>\n"
        f"🟡 <b>Medium Words:</b> <code>{len(WORDS['medium'])}</code>\n"
        f"🔴 <b>Hard Words:</b> <code>{len(WORDS['hard'])}</code>\n\n"
        "📌 <b>Bulk Words Add karne ke liye:</b>\n"
        "<code>/word easy cat dog bird tree</code>\n\n"
        "Neeche buttons par click karke category ke words check karein (Single-tap copy):",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )
    asyncio.create_task(delete_after(message, 3))

# ============================================================
# SETTINGS PANEL
# ============================================================

@app.on_message(filters.command("settings"))
async def settings_cmd(_, message: Message):
    if not await is_admin_or_owner(message.chat, message.from_user.id):
        return await message.reply_text("❌ Only group admins can configure settings.")

    s = get_settings(message.chat.id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ Stop Game", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ Start Game", callback_data="set_start_game")
    del_btn = InlineKeyboardButton("🗑️ Auto-Del: ON", callback_data="set_toggle_autodel") if s["auto_delete"] else InlineKeyboardButton("🗑️ Auto-Del: OFF", callback_data="set_toggle_autodel")

    p_easy = get_global_config("points_easy", 10)
    p_med = get_global_config("points_medium", 20)
    p_hard = get_global_config("points_hard", 30)

    h_easy = get_global_config("hints_easy", 3)
    h_med = get_global_config("hints_medium", 3)
    h_hard = get_global_config("hints_hard", 3)

    kb = InlineKeyboardMarkup([
        [
            status_btn,
            InlineKeyboardButton(f"Mode: {str(cur_diff).upper()}", callback_data="set_menu_mode")
        ],
        [
            InlineKeyboardButton("⏱️ Timers", callback_data="set_menu_timers"),
            del_btn
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
        ]
    ])
    await message.reply_text(
        f"⚙️ <b>Jumble Group Settings</b>\n\n"
        f"🟢 Game Status: <b>{'Running' if s['is_active'] else 'Stopped'}</b>\n"
        f"🗑️ Auto Delete Old: <b>{'Enabled' if s['auto_delete'] else 'Disabled'}</b>\n"
        f"🎯 Default Mode: <b>{str(cur_diff).title()}</b>\n"
        f"⏱️ Timers: Easy: <b>{s['easy']}s</b> | Med: <b>{s['medium']}s</b> | Hard: <b>{s['hard']}s</b>\n\n"
        f"🌍 <b>Global Rewards:</b> Easy: <b>{p_easy}pts</b> | Med: <b>{p_med}pts</b> | Hard: <b>{p_hard}pts</b>\n"
        f"💡 <b>Global Hints:</b> Easy: <b>{h_easy}</b> | Med: <b>{h_med}</b> | Hard: <b>{h_hard}</b>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("jumble"))
async def jumble_cmd(_, message: Message):
    ensure_user(message.from_user)
    if message.chat.id in JUMBLE_FIGHT:
        return await message.reply_text("⚔️ Jumble Fight chal rahi hai, match khatam hone tak wait karein.")

    DB.execute("UPDATE settings SET is_active=1 WHERE chat_id=?", (message.chat.id,))
    DB.commit()

    s = get_settings(message.chat.id)
    default_d = s["default_diff"] if "default_diff" in s.keys() else "medium"
    difficulty = message.command[1].lower() if len(message.command) > 1 else default_d
    
    if difficulty not in WORDS:
        difficulty = "medium"

    await start_game(message.chat.id, difficulty, message)

@app.on_message(filters.command(["jumblefight", "fight", "rapido"]))
async def jumble_fight_cmd(_, message: Message):
    if not is_group(message):
        return await message.reply_text("❌ Jumble Fight sirf groups mein chal sakta hai.")

    target_user = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        arg = message.command[1]
        try:
            if arg.isdigit():
                target_user = await app.get_users(int(arg))
            else:
                target_user = await app.get_users(arg)
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    elif message.entities:
        for entity in message.entities:
            if entity.type.name == "TEXT_MENTION" and entity.user:
                target_user = entity.user
                break

    if not target_user:
        return await message.reply_text("Usage:\n• <code>/jumblefight @username</code>\n• <code>/jumblefight UserID</code>\n• Reply to a user with <code>/jumblefight</code>", parse_mode=ParseMode.HTML)

    if target_user.id == message.from_user.id:
        return await message.reply_text("❌ Khud ke sath fight nahi kar sakte.")

    if target_user.is_bot:
        return await message.reply_text("❌ Bots ke sath match nahi ho sakta.")

    ensure_user(message.from_user)
    ensure_user(target_user)

    key = message.chat.id
    if key in JUMBLE_FIGHT:
        return await message.reply_text("⚔️ Is group mein already Jumble Fight chal rahi hai.")

    m1 = get_mention(message.from_user)
    m2 = get_mention(target_user)

    FIGHT_LOBBY[key] = {
        "p1": message.from_user.id,
        "p2": target_user.id,
        "p1_name": message.from_user.first_name,
        "p2_name": target_user.first_name,
        "m1": m1,
        "m2": m2,
        "difficulty": "medium",
        "timer": 60
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Easy", callback_data="f_diff_easy"),
            InlineKeyboardButton("🟡 Medium", callback_data="f_diff_medium"),
            InlineKeyboardButton("🔴 Hard", callback_data="f_diff_hard")
        ],
        [
            InlineKeyboardButton("⏱️ 30s", callback_data="f_time_30"),
            InlineKeyboardButton("⏱️ 45s", callback_data="f_time_45"),
            InlineKeyboardButton("⏱️ 60s", callback_data="f_time_60")
        ],
        [
            InlineKeyboardButton("✅ Accept Challenge", callback_data="f_accept"),
            InlineKeyboardButton("❌ Decline", callback_data="f_decline")
        ]
    ])

    await message.reply_text(
        f"⚔️ <b>JUMBLE FIGHT 1v1 CHALLENGE!</b>\n\n"
        f"👤 <b>Challenger:</b> {m1} (<code>{message.from_user.id}</code>)\n"
        f"🎯 <b>Target:</b> {m2} (<code>{target_user.id}</code>)\n\n"
        f"⚙️ <b>Settings:</b> Mode: <b>Medium</b> | Timer: <b>60s</b>\n\n"
        f"👉 {m2}, match shuru karne ke liye <b>Accept Challenge</b> par click karo!",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    total = u["fight_wins"] + u["fight_losses"]
    winrate = ((u["fight_wins"] / total) * 100) if total else 0
    mention = get_mention(message.from_user)
    priv_status = "🔒 Private" if u["is_private"] else "🌐 Public"

    await message.reply_text(
        f"👤 {mention} (<code>{message.from_user.id}</code>)\n\n"
        f"⭐ Points: <b>{u['points']}</b>\n"
        f"🧩 Solved: <b>{u['solved']}</b>\n"
        f"🔥 Streak: <b>{u['streak']}</b> (Best: {u['best_streak']})\n"
        f"🛡️ Privacy: <b>{priv_status}</b>\n\n"
        f"⚔️ Jumble Fight: <b>{u['fight_wins']}W - {u['fight_losses']}L</b>\n"
        f"📈 Win Rate: <b>{winrate:.1f}%</b>",
        parse_mode=ParseMode.HTML
    )

# ============================================================
# DYNAMIC LEADERBOARD SYSTEM
# ============================================================

def format_lb_entry(user_id, name, username, is_private):
    clean_name = (name or "Player").replace("<", "&lt;").replace(">", "&gt;")
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
        title = "📅 <b>DAILY GROUP LEADERBOARD (24h)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            JOIN users u ON h.user_id = u.user_id
            WHERE h.chat_id = ? AND h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (chat_id, since)).fetchall()
        
    elif scope_type == "weekly":
        since = now - (86400 * 7)
        title = "🗓️ <b>WEEKLY GROUP LEADERBOARD (7 Days)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            JOIN users u ON h.user_id = u.user_id
            WHERE h.chat_id = ? AND h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (chat_id, since)).fetchall()

    elif scope_type == "monthly":
        since = now - (86400 * 30)
        title = "📆 <b>MONTHLY GLOBAL LEADERBOARD (30 Days)</b>"
        rows = DB.execute("""
            SELECT h.user_id, u.name, u.username, u.is_private, SUM(h.points) as total_pts
            FROM score_history h
            JOIN users u ON h.user_id = u.user_id
            WHERE h.timestamp >= ?
            GROUP BY h.user_id
            ORDER BY total_pts DESC
            LIMIT 10
        """, (since,)).fetchall()

    else:
        title = "🌍 <b>GLOBAL ALL-TIME LEADERBOARD</b>"
        rows = DB.execute("""
            SELECT user_id, name, username, is_private, points as total_pts
            FROM users
            ORDER BY points DESC
            LIMIT 10
        """).fetchall()

    text = f"{title}\n\n"
    if not rows:
        text += "<i>Abhi tak koi score record nahi hua hai.</i>"
    else:
        for i, u in enumerate(rows, 1):
            medal = medals[i - 1] if i <= 3 else f"<code>{i}.</code>"
            user_entry = format_lb_entry(u['user_id'], u['name'], u['username'], u['is_private'])
            text += f"{medal} {user_entry} — ⭐ <b>{u['total_pts']} pts</b>\n"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{'✅ ' if scope_type=='daily' else ''}📅 Daily (GC)", callback_data=f"lb_daily_{chat_id}"),
            InlineKeyboardButton(f"{'✅ ' if scope_type=='weekly' else ''}🗓️ Weekly (GC)", callback_data=f"lb_weekly_{chat_id}")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if scope_type=='monthly' else ''}📆 Monthly (Global)", callback_data=f"lb_monthly_{chat_id}"),
            InlineKeyboardButton(f"{'✅ ' if scope_type=='global' else ''}🌍 Global", callback_data=f"lb_global_{chat_id}")
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
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
# UNIFIED ANSWER HANDLER
# ============================================================

@app.on_message(
    filters.text &
    ~filters.command([
        "start", "help", "jumble", "stats", "leaderboard", "top", "rank", "lb",
        "jumblefight", "fight", "rapido", "settings", "setpoints", "sethint", "setdaily", "setbonus",
        "daily", "bonus", "private", "public",
        "addword", "delword", "word", "words", "auth", "unauth", "authlist", "update", "gitpull"
    ])
)
async def unified_answer_handler(_, message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    cleaned_input = clean_answer(message.text)

    # 1. Active Jumble Fight Check
    if chat_id in JUMBLE_FIGHT:
        async with LOCK:
            game = JUMBLE_FIGHT.get(chat_id)
            if not game or user_id not in game["players"]:
                return

            if time.time() <= game["expires"] and cleaned_input == clean_answer(game["word"]):
                curr = asyncio.current_task()
                if game.get("task") and game["task"] is not curr and not game["task"].done():
                    try:
                        game["task"].cancel()
                    except Exception:
                        pass

                game["scores"][user_id] += 1
                
                s = get_settings(chat_id)
                if s["auto_delete"] and game.get("msg_id"):
                    await safe_delete_and_unpin(chat_id, game["msg_id"])

                u_mention = get_mention(message.from_user)
                r_msg = await message.reply_text(
                    f"⚡ {u_mention} (<code>{user_id}</code>) <b>WON ROUND {game['round']}!</b>\n"
                    f"🏆 Round Score: <b>{game['scores'][user_id]}</b>",
                    parse_mode=ParseMode.HTML
                )
                if s["auto_delete"]:
                    asyncio.create_task(delete_after(r_msg, 4))
                    
                await asyncio.sleep(2.5)
                asyncio.create_task(fight_next(chat_id))
                return
        return

    # 2. Normal Game Check
    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game or time.time() > game["expires"]:
        return

    if cleaned_input == clean_answer(game["word"]):
        updated = DB.execute("UPDATE games SET solved=1 WHERE chat_id=? AND solved=0", (chat_id,))
        if updated.rowcount != 1:
            return
        DB.commit()

        ensure_user(message.from_user)
        u = get_user(user_id)
        settings = get_settings(chat_id)
        pts_reward = get_global_config(f"points_{game['difficulty']}", 10)

        new_streak = u["streak"] + 1
        best = max(new_streak, u["best_streak"])

        # Update Master Score
        DB.execute("""
            UPDATE users
            SET points=points+?, solved=solved+1, streak=?, best_streak=?
            WHERE user_id=?
        """, (pts_reward, new_streak, best, user_id))
        
        # Track Time-series Score History
        DB.execute("""
            INSERT INTO score_history (user_id, chat_id, points, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, chat_id, pts_reward, time.time()))
        DB.commit()

        if settings["auto_delete"] and game["message_id"]:
            await safe_delete_and_unpin(chat_id, game["message_id"])

        u_mention = get_mention(message.from_user)
        c_msg = await message.reply_text(
            f"🎉 <b>CORRECT!</b>\n\n"
            f"👤 {u_mention} (<code>{user_id}</code>)\n"
            f"✅ Answer: <b>{game['word'].upper()}</b>\n"
            f"⭐ <b>+{pts_reward} points</b>\n"
            f"🔥 Current Streak: <b>{new_streak}</b>\n\n"
            f"🔄 Next puzzle coming in 3 seconds...",
            parse_mode=ParseMode.HTML
        )

        if settings["auto_delete"]:
            asyncio.create_task(delete_after(c_msg, 4))

        await asyncio.sleep(3)
        s = get_settings(chat_id)
        if chat_id not in JUMBLE_FIGHT and s["is_active"]:
            asyncio.create_task(start_game(chat_id, game["difficulty"], chat_id))

# ============================================================
# CALLBACK QUERIES
# ============================================================

@app.on_callback_query()
async def callback_router(_, query: CallbackQuery):
    data = query.data
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    if data == "hint":
        game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
        if not game:
            return await query.answer("Koi active puzzle nahi hai.", show_alert=True)

        ensure_user(query.from_user)
        puzzle_id = game["puzzle_id"]
        word = game["word"]
        difficulty = game["difficulty"]

        hint_limit = get_global_config(f"hints_{difficulty}", 3)

        hint_row = DB.execute("SELECT * FROM puzzle_hints WHERE chat_id=? AND puzzle_id=? AND user_id=?", (chat_id, puzzle_id, user_id)).fetchone()
        hints_used = hint_row["hints_used"] if hint_row else 0
        revealed_indices = [int(i) for i in hint_row["revealed_indices"].split(",") if i] if hint_row else []

        if hints_used >= hint_limit:
            return await query.answer(f"❌ Is word ke liye aapki {hint_limit} hints complete ho chuki hain!", show_alert=True)

        available_indices = [i for i in range(len(word)) if i not in revealed_indices]
        if not available_indices:
            return await query.answer("❌ Aur hints available nahi hain.", show_alert=True)

        chosen_index = random.choice(available_indices)
        revealed_indices.append(chosen_index)
        hints_used += 1

        DB.execute("""
            INSERT INTO puzzle_hints(chat_id, puzzle_id, user_id, hints_used, revealed_indices)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, puzzle_id, user_id) DO UPDATE SET
                hints_used=excluded.hints_used,
                revealed_indices=excluded.revealed_indices
        """, (chat_id, puzzle_id, user_id, hints_used, ",".join(map(str, revealed_indices))))
        DB.commit()

        letter = word[chosen_index].upper()
        return await query.answer(f"💡 Hint: Letter #{chosen_index + 1} is '{letter}'\nRemaining: {hint_limit - hints_used}/{hint_limit}", show_alert=True)

    elif data == "fight_hint":
        game = JUMBLE_FIGHT.get(chat_id)
        if not game or user_id not in game["players"]:
            return await query.answer("❌ Sirf match players hints le sakte hain.", show_alert=True)

        difficulty = game["difficulty"]
        hint_limit = get_global_config(f"hints_{difficulty}", 3)

        p_hint = game["round_hints"][user_id]
        if p_hint["count"] >= hint_limit:
            return await query.answer(f"❌ Is round ke {hint_limit} hints use ho chuke hain!", show_alert=True)

        word = game["word"]
        avail = [i for i in range(len(word)) if i not in p_hint["indices"]]
        if not avail:
            return await query.answer("❌ Aur letters reveal nahi ho sakte.", show_alert=True)

        idx = random.choice(avail)
        p_hint["indices"].append(idx)
        p_hint["count"] += 1

        return await query.answer(f"💡 Hint: Letter #{idx + 1} is '{word[idx].upper()}'\nRemaining: {hint_limit - p_hint['count']}/{hint_limit}", show_alert=True)

    # ============================================================
    # LEADERBOARD CALLBACK ROUTER
    # ============================================================
    elif data.startswith("lb_"):
        await query.answer()
        parts = data.split("_")
        scope = parts[1]
        target_chat = int(parts[2])

        text, kb = build_leaderboard_text_and_kb(scope, target_chat)
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass

    # ============================================================
    # WORD BANK PAGINATED VIEWER
    # ============================================================
    elif data.startswith("wb_"):
        await query.answer()
        if not is_authed(user_id):
            return await query.answer("❌ Sirf Auth Users word bank dekh sakte hain.", show_alert=True)

        _, diff, page_str = data.split("_")
        page = int(page_str)
        word_list = sorted(WORDS.get(diff, []))
        total_words = len(word_list)
        per_page = 20
        total_pages = max(1, (total_words + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_words = word_list[start_idx:end_idx]

        formatted_list = "  •  ".join(f"<code>{w.upper()}</code>" for w in page_words)

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"wb_{diff}_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop_page"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"wb_{diff}_{page + 1}"))

        kb = InlineKeyboardMarkup([
            nav_row,
            [
                InlineKeyboardButton("🟢 Easy", callback_data="wb_easy_1"),
                InlineKeyboardButton("🟡 Medium", callback_data="wb_medium_1"),
                InlineKeyboardButton("🔴 Hard", callback_data="wb_hard_1")
            ],
            [
                InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_words_menu"),
                InlineKeyboardButton("❌ Close", callback_data="close_panel")
            ]
        ])

        msg = (
            f"📚 <b>{diff.upper()} WORDS BANK</b> (Total: <code>{total_words}</code>)\n"
            f"📌 <i>Tip: Tap on any word below to copy it!</i>\n\n"
            f"{formatted_list}\n\n"
            f"➕ Add: <code>/addword {diff} word</code>\n"
            f"➖ Del: <code>/delword {diff} word</code>"
        )

        try:
            await query.message.edit_text(msg, reply_markup=kb, parse_mode=ParseMode.HTML)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await app.send_message(chat_id, msg, reply_markup=kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

    elif data == "noop_page":
        await query.answer("Current Page Number", show_alert=False)

    elif data == "back_to_words_menu":
        await query.answer()
        if not is_authed(user_id):
            return await query.answer("❌ Authorized users only.", show_alert=True)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🟢 Easy ({len(WORDS['easy'])})", callback_data="wb_easy_1"),
                InlineKeyboardButton(f"🟡 Medium ({len(WORDS['medium'])})", callback_data="wb_medium_1"),
                InlineKeyboardButton(f"🔴 Hard ({len(WORDS['hard'])})", callback_data="wb_hard_1")
            ],
            [
                InlineKeyboardButton("❌ Close", callback_data="close_panel")
            ]
        ])
        try:
            await query.message.edit_text(
                "📚 <b>JUMBLE WORD BANK</b>\n\n"
                f"🟢 <b>Easy Words:</b> <code>{len(WORDS['easy'])}</code>\n"
                f"🟡 <b>Medium Words:</b> <code>{len(WORDS['medium'])}</code>\n"
                f"🔴 <b>Hard Words:</b> <code>{len(WORDS['hard'])}</code>\n\n"
                "📌 <b>Bulk Words Add karne ke liye:</b>\n"
                "<code>/word easy cat dog bird tree</code>\n\n"
                "Neeche buttons par click karke category ke words check karein (Single-tap copy):",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    # ============================================================
    # FIGHT ACCEPT & SETUP HANDLERS
    # ============================================================
    elif data.startswith("f_"):
        lobby = FIGHT_LOBBY.get(chat_id)
        if not lobby:
            return await query.answer("Match lobby expire ho chuki hai.", show_alert=True)

        if data == "f_decline":
            if user_id != lobby["p2"] and user_id != lobby["p1"] and not await is_admin_or_owner(query.message.chat, user_id):
                return await query.answer("❌ Sirf match players hi decline kar sakte hain.", show_alert=True)
            
            del FIGHT_LOBBY[chat_id]
            await query.message.delete()
            return await query.answer("Challenge declined.")

        if data == "f_accept":
            if user_id != lobby["p2"]:
                return await query.answer("❌ Yeh challenge aapke liye nahi hai! Sirf opponent accept kar sakta hai.", show_alert=True)

            JUMBLE_FIGHT[chat_id] = {
                "players": [lobby["p1"], lobby["p2"]],
                "names": {lobby["p1"]: lobby["p1_name"], lobby["p2"]: lobby["p2_name"]},
                "mentions": {lobby["p1"]: lobby["m1"], lobby["p2"]: lobby["m2"]},
                "round": 0,
                "scores": defaultdict(int),
                "word": None,
                "expires": None,
                "task": None,
                "difficulty": lobby["difficulty"],
                "timer": lobby["timer"],
                "msg_id": None
            }
            del FIGHT_LOBBY[chat_id]
            
            await query.message.delete()
            await query.answer("🚀 Challenge Accepted!")

            announcement = await app.send_message(
                chat_id,
                f"🔥 <b>Challenge Accepted by {lobby['m2']}!</b>\n\n"
                f"⚔️ <b>{lobby['m1']}</b> 🆚 <b>{lobby['m2']}</b>\n"
                f"🚀 Match starting in <b>3 seconds...</b>",
                parse_mode=ParseMode.HTML
            )
            asyncio.create_task(delete_after(announcement, 4))
            
            await asyncio.sleep(3)
            asyncio.create_task(fight_next(chat_id))
            return

        if user_id not in (lobby["p1"], lobby["p2"]) and not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Match players hi settings change kar sakte hain.", show_alert=True)

        if data.startswith("f_diff_"):
            lobby["difficulty"] = data.split("_")[2]
            await query.answer(f"Difficulty set to {lobby['difficulty'].upper()}")
        elif data.startswith("f_time_"):
            lobby["timer"] = int(data.split("_")[2])
            await query.answer(f"Timer set to {lobby['timer']}s")

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='easy' else ''}Easy", callback_data="f_diff_easy"),
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='medium' else ''}Medium", callback_data="f_diff_medium"),
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='hard' else ''}Hard", callback_data="f_diff_hard")
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==30 else ''}30s", callback_data="f_time_30"),
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==45 else ''}45s", callback_data="f_time_45"),
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==60 else ''}60s", callback_data="f_time_60")
            ],
            [
                InlineKeyboardButton("✅ Accept Challenge", callback_data="f_accept"),
                InlineKeyboardButton("❌ Decline", callback_data="f_decline")
            ]
        ])
        try:
            await query.message.edit_text(
                f"⚔️ <b>JUMBLE FIGHT 1v1 CHALLENGE!</b>\n\n"
                f"👤 <b>Challenger:</b> {lobby['m1']} (<code>{lobby['p1']}</code>)\n"
                f"🎯 <b>Target:</b> {lobby['m2']} (<code>{lobby['p2']}</code>)\n\n"
                f"⚙️ <b>Settings:</b> Mode: <b>{lobby['difficulty'].title()}</b> | Timer: <b>{lobby['timer']}s</b>\n\n"
                f"👉 {lobby['m2']}, match shuru karne ke liye <b>Accept Challenge</b> par click karo!",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        except MessageNotModified:
            pass

    elif data.startswith("set_"):
        if not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Only admins can change settings.", show_alert=True)

        if data == "set_start_game":
            DB.execute("UPDATE settings SET is_active=1 WHERE chat_id=?", (chat_id,))
            DB.commit()
            await query.answer("▶️ Game started!")
            await show_settings_panel(query.message, chat_id)
            s = get_settings(chat_id)
            asyncio.create_task(start_game(chat_id, s["default_diff"], query.message))

        elif data == "set_stop_game":
            DB.execute("UPDATE settings SET is_active=0 WHERE chat_id=?", (chat_id,))
            old_g = DB.execute("SELECT message_id FROM games WHERE chat_id=?", (chat_id,)).fetchone()
            s = get_settings(chat_id)
            if old_g and s["auto_delete"] and old_g["message_id"]:
                await safe_delete_and_unpin(chat_id, old_g["message_id"])
            DB.execute("DELETE FROM games WHERE chat_id=?", (chat_id,))
            DB.commit()
            await query.answer("⏹️ Game stopped!")
            await show_settings_panel(query.message, chat_id)

        elif data == "set_toggle_autodel":
            s = get_settings(chat_id)
            new_val = 0 if s["auto_delete"] else 1
            DB.execute("UPDATE settings SET auto_delete=? WHERE chat_id=?", (new_val, chat_id))
            DB.commit()
            await query.answer(f"Auto Delete {'Enabled' if new_val else 'Disabled'}")
            await show_settings_panel(query.message, chat_id)

        elif data == "set_menu_mode":
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🟢 Easy", callback_data="set_def_easy"),
                    InlineKeyboardButton("🟡 Medium", callback_data="set_def_medium"),
                    InlineKeyboardButton("🔴 Hard", callback_data="set_def_hard")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_back")]
            ])
            try:
                await query.message.edit_text("🎯 <b>Default Jumble Difficulty Chuno:</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass

        elif data == "set_menu_timers":
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Easy: 60s", callback_data="set_t_easy_60"),
                    InlineKeyboardButton("Easy: 120s", callback_data="set_t_easy_120")
                ],
                [
                    InlineKeyboardButton("Med: 180s", callback_data="set_t_medium_180"),
                    InlineKeyboardButton("Med: 300s", callback_data="set_t_medium_300")
                ],
                [
                    InlineKeyboardButton("Hard: 300s", callback_data="set_t_hard_300"),
                    InlineKeyboardButton("Hard: 600s", callback_data="set_t_hard_600")
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="set_back")]
            ])
            try:
                await query.message.edit_text("⏱️ <b>Select Timer Duration:</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass

        elif data.startswith("set_def_"):
            d = data.split("_")[2]
            DB.execute("UPDATE settings SET default_diff=? WHERE chat_id=?", (d, chat_id))
            DB.commit()
            await query.answer(f"Default mode set to {d.upper()}")
            await show_settings_panel(query.message, chat_id)

        elif data.startswith("set_t_"):
            _, _, diff, secs = data.split("_")
            DB.execute(f"UPDATE settings SET {diff}=? WHERE chat_id=?", (int(secs), chat_id))
            DB.commit()
            await query.answer(f"{diff.title()} timer updated to {secs}s")
            await show_settings_panel(query.message, chat_id)

        elif data == "set_back":
            await show_settings_panel(query.message, chat_id)

    elif data == "skip":
        if not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Only admins/owner can skip.", show_alert=True)

        game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
        if not game:
            return await query.answer("Active game nahi mila.", show_alert=True)

        DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
        DB.commit()
        
        s = get_settings(chat_id)
        if s["auto_delete"] and game["message_id"]:
            await safe_delete_and_unpin(chat_id, game["message_id"])

        sk_msg = await query.message.reply_text(f"⏭️ <b>Skipped!</b>\nAnswer: <b>{game['word'].upper()}</b>\n\n🔄 Next puzzle starting in 3 seconds...", parse_mode=ParseMode.HTML)
        if s["auto_delete"]:
            asyncio.create_task(delete_after(sk_msg, 4))
            
        await query.answer("Skipped.")
        await asyncio.sleep(3)
        s = get_settings(chat_id)
        if s["is_active"]:
            asyncio.create_task(start_game(chat_id, game["difficulty"], chat_id))

    elif data == "newword":
        old = DB.execute("SELECT * FROM games WHERE chat_id=?", (chat_id,)).fetchone()
        if old and not old["solved"] and time.time() <= old["expires"]:
            return await query.answer("❌ Current puzzle abhi active hai.", show_alert=True)

        s = get_settings(chat_id)
        difficulty = old["difficulty"] if old else s["default_diff"]
        await query.answer("🧩 Starting new puzzle...")
        asyncio.create_task(start_game(chat_id, difficulty, query.message))

    elif data == "close_panel":
        await query.message.delete()

async def show_settings_panel(message_obj, chat_id):
    s = get_settings(chat_id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ Stop Game", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ Start Game", callback_data="set_start_game")
    del_btn = InlineKeyboardButton("🗑️ Auto-Del: ON", callback_data="set_toggle_autodel") if s["auto_delete"] else InlineKeyboardButton("🗑️ Auto-Del: OFF", callback_data="set_toggle_autodel")

    p_easy = get_global_config("points_easy", 10)
    p_med = get_global_config("points_medium", 20)
    p_hard = get_global_config("points_hard", 30)

    h_easy = get_global_config("hints_easy", 3)
    h_med = get_global_config("hints_medium", 3)
    h_hard = get_global_config("hints_hard", 3)

    kb = InlineKeyboardMarkup([
        [
            status_btn,
            InlineKeyboardButton(f"Mode: {str(cur_diff).upper()}", callback_data="set_menu_mode")
        ],
        [
            InlineKeyboardButton("⏱️ Timers", callback_data="set_menu_timers"),
            del_btn
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
        ]
    ])
    text = (
        f"⚙️ <b>Jumble Group Settings</b>\n\n"
        f"🟢 Game Status: <b>{'Running' if s['is_active'] else 'Stopped'}</b>\n"
        f"🗑️ Auto Delete Old: <b>{'Enabled' if s['auto_delete'] else 'Disabled'}</b>\n"
        f"🎯 Default Mode: <b>{str(cur_diff).title()}</b>\n"
        f"⏱️ Timers: Easy: <b>{s['easy']}s</b> | Med: <b>{s['medium']}s</b> | Hard: <b>{s['hard']}s</b>\n\n"
        f"🌍 <b>Global Rewards:</b> Easy: <b>{p_easy}pts</b> | Med: <b>{p_med}pts</b> | Hard: <b>{p_hard}pts</b>\n"
        f"💡 <b>Global Hints:</b> Easy: <b>{h_easy}</b> | Med: <b>{h_med}</b> | Hard: <b>{h_hard}</b>"
    )
    try:
        await message_obj.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except MessageNotModified:
        pass

# ============================================================
# AUTO-RESUME GAMES ON BOT STARTUP
# ============================================================

async def resume_all_active_games():
    await asyncio.sleep(4)
    rows = DB.execute("SELECT chat_id, default_diff FROM settings WHERE is_active = 1").fetchall()
    for row in rows:
        c_id = row["chat_id"]
        diff = row["default_diff"] or "medium"
        try:
            existing = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (c_id,)).fetchone()
            if not existing or time.time() > existing["expires"]:
                asyncio.create_task(start_game(c_id, diff, c_id))
        except Exception as e:
            print(f"Error resuming chat {c_id}: {e}")

# ============================================================
# RUN BOT
# ============================================================

async def main():
    await app.start()
    print("🚀 Jumble Fight & Jumble Bot Started Successfully (with Global Points & Hints)!")
    asyncio.create_task(resume_all_active_games())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
