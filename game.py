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
from pyrogram.enums import ChatType, ChatMemberStatus
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
    fight_losses INTEGER DEFAULT 0
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
    points_per_word INTEGER DEFAULT 10,
    default_diff TEXT DEFAULT 'medium',
    is_active INTEGER DEFAULT 1,
    auto_delete INTEGER DEFAULT 0
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
""")
DB.commit()

def run_migrations():
    cols = [c[1] for c in DB.execute("PRAGMA table_info(settings)").fetchall()]
    if "default_diff" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN default_diff TEXT DEFAULT 'medium'")
    if "points_per_word" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN points_per_word INTEGER DEFAULT 10")
    if "is_active" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN is_active INTEGER DEFAULT 1")
    if "auto_delete" not in cols:
        DB.execute("ALTER TABLE settings ADD COLUMN auto_delete INTEGER DEFAULT 0")
    
    user_cols = [c[1] for c in DB.execute("PRAGMA table_info(users)").fetchall()]
    if "fight_wins" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN fight_wins INTEGER DEFAULT 0")
    if "fight_losses" not in user_cols:
        DB.execute("ALTER TABLE users ADD COLUMN fight_losses INTEGER DEFAULT 0")
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
    return user_id == OWNER_ID

def is_authed(user_id):
    if is_owner(user_id):
        return True
    row = DB.execute("SELECT user_id FROM auth_users WHERE user_id=?", (user_id,)).fetchone()
    return bool(row)

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
            INSERT INTO settings(chat_id, easy, medium, hard, points_per_word, default_diff, is_active, auto_delete)
            VALUES (?, 120, 300, 600, 10, 'medium', 1, 0)
            ON CONFLICT(chat_id) DO NOTHING
        """, (chat_id,))
        DB.commit()
        row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    return row

def is_group(message):
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) or str(message.chat.type).lower() in ("group", "supergroup", "chattype.group", "chattype.supergroup")

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
            InlineKeyboardButton("💡 Hint (3/word)", callback_data="hint"),
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
    expires = now + timer_val

    DB.execute("""
        INSERT INTO games(chat_id, difficulty, word, puzzle_id, started, expires, message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, difficulty, word, puzzle_id, now, expires, 0))
    DB.commit()

    image = make_puzzle_image(jumbled, difficulty, puzzle_id)
    caption_text = (
        f"🧩 **Jumble #{puzzle_id}**\n\n"
        f"🎯 Difficulty: **{difficulty.title()}**\n"
        f"⏱️ Time: **{timer_val // 60} min {timer_val % 60} sec**\n"
        f"⭐ Reward: **+{settings['points_per_word']} Points**\n\n"
        f"🔀 Unscramble the letters!\n"
        f"💬 Type your answer in the chat."
    )

    try:
        if isinstance(message_or_chat, Message):
            sent = await message_or_chat.reply_photo(photo=image, caption=caption_text, reply_markup=normal_keyboard())
        else:
            sent = await app.send_photo(chat_id, photo=image, caption=caption_text, reply_markup=normal_keyboard())

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
            f"⏰ **Time's Up!**\n\n❌ Nobody solved it.\n✅ Answer: **{row['word'].upper()}**\n\n🔄 Next puzzle starting in 3 seconds..."
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
            InlineKeyboardButton("💡 Hint (3/word)", callback_data="fight_hint")
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
                    f"⏰ **Round {round_num} Timeout!**\n❌ Kisi ne solve nahi kiya.\n✅ Answer: **{word.upper()}**\n\n🔄 Next round starting..."
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
                f"⚔️ **JUMBLE FIGHT — ROUND {game['round']}/10**\n\n"
                f"🎯 Difficulty: **{diff.title()}**\n"
                f"⏱️ Time: **{game['timer']}s**\n"
                f"🔀 Solve fastest!\n"
                f"👥 Players: {game['names'][game['players'][0]]} 🆚 {game['names'][game['players'][1]]}"
            ),
            reply_markup=fight_keyboard()
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

    n1, n2 = game["names"][p1], game["names"][p2]
    result = f"🏁 **JUMBLE FIGHT OVER!**\n\n👤 **{n1}** — {s1} pts\n👤 **{n2}** — {s2} pts\n\n"

    if winner:
        result += f"🏆 Match Winner: **{game['names'][winner]}** 🎉"
    else:
        result += "🤝 **Match Draw!**"

    await app.send_message(chat_id, result)

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if s["is_active"]:
        await app.send_message(chat_id, "🔄 Resuming normal Jumble Game...")
        asyncio.create_task(start_game(chat_id, s["default_diff"], chat_id))

# ============================================================
# COMMAND HANDLERS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    ensure_user(message.from_user)
    text = (
        "🧩 **Welcome to Advanced Jumble Bot!**\n\n"
        "🎮 **Game Commands:**\n"
        "• `/jumble` — Start Auto-loop Jumble Game\n"
        "• `/jumblefight @user` — 1v1 Battle Mode\n"
        "• `/settings` — Admin Panel (Start/Stop, Mode, Auto-delete)\n\n"
        "📊 **Stats & Rankings:**\n"
        "• `/stats` — Your Performance\n"
        "• `/leaderboard` — Top Global Players\n"
        "• `/help` — Full Bot Guide"
    )

    dm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Support Group", url=SUPPORT_GC)]
    ])

    if message.chat.type in (ChatType.PRIVATE,):
        try:
            await message.reply_photo(photo=START_IMG, caption=text, reply_markup=dm_markup)
        except Exception:
            await message.reply_text(text, reply_markup=dm_markup)
    else:
        await message.reply_text(text)

@app.on_message(filters.command("help"))
async def help_cmd(_, message: Message):
    is_user_auth = is_authed(message.from_user.id)
    text = (
        "🧩 **Jumble Commands Guide**\n\n"
        "`/jumble` — Start auto-looping jumble game\n"
        "`/jumblefight @user` — 1v1 battle match\n"
        "`/settings` — Admin start/stop and game settings\n"
        "`/leaderboard` — Top players ranking\n"
        "`/stats` — Personal score card\n\n"
        "💡 Har word par aapko **3 fresh hints** milti hain.\n"
    )
    if is_user_auth:
        text += (
            "\n🔐 **Auth / Word Bank Commands:**\n"
            "`/word` — View categorized word bank (with Pagination & Single-tap copy)\n"
            "`/addword easy word` — Add new word to bank\n"
            "`/delword easy word` — Delete word from bank\n"
            "`/update` — Update bot from GitHub repository\n"
        )
    if is_owner(message.from_user.id):
        text += (
            "\n👑 **Owner Commands:**\n"
            "`/auth @user` — Grant auth access\n"
            "`/unauth @user` — Revoke auth access\n"
            "`/authlist` — List of authorized users\n"
            "`/setpoints 20` — Set per-word reward\n"
        )
    await message.reply_text(text)

# ============================================================
# GIT UPDATER (AUTH / OWNER ONLY)
# ============================================================

@app.on_message(filters.command(["update", "gitpull"]))
async def update_bot_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Authorized users bot update kar sakte hain.")

    msg = await message.reply_text("🔄 **Pulling latest changes from GitHub...**")
    try:
        subprocess.run(["git", "stash"], check=True, capture_output=True, text=True)
        pull_res = subprocess.run(["git", "pull"], check=True, capture_output=True, text=True)
        out = pull_res.stdout or "Updated successfully."
        
        await msg.edit_text(f"✅ **Git Pull Output:**\n`{out[:500]}`\n\n🚀 **Restarting bot instance...**")
        await asyncio.sleep(1.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit_text(f"❌ **Update Failed:**\n`{str(e)}`")

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
            target = await app.get_users(message.command[1])
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    else:
        return await message.reply_text("Usage:\n`/auth @username` or Reply `/auth`")

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

    res = await message.reply_text(f"✅ **{target.first_name}** ko **Auth Access** de diya gaya.")
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
            target = await app.get_users(message.command[1])
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    else:
        return await message.reply_text("Usage:\n`/unauth @username` or Reply `/unauth`")

    DB.execute("DELETE FROM auth_users WHERE user_id=?", (target.id,))
    DB.commit()

    res = await message.reply_text(f"🚫 **{target.first_name}** ka auth access remove kar diya gaya.")
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("authlist"))
async def authlist_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner aur Auth users authlist dekh sakte hain.")

    rows = DB.execute("SELECT * FROM auth_users ORDER BY added_at DESC").fetchall()
    text = "🔐 **AUTHORIZED USERS LIST**\n\n"
    text += f"👑 **Owner:** `{OWNER_ID}`\n\n"

    if not rows:
        text += "Koi extra authorized user nahi hai."
    else:
        for i, row in enumerate(rows, 1):
            uname = f"@{row['username']}" if row['username'] else "No Username"
            text += f"`{i}.` **{row['name']}** ({uname}) — ID: `{row['user_id']}`\n"

    res = await message.reply_text(text)
    asyncio.create_task(delete_after(message, 10))
    asyncio.create_task(delete_after(res, 10))

# ============================================================
# WORD BANK MANAGEMENT
# ============================================================

@app.on_message(filters.command("addword"))
async def addword_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

    if len(message.command) < 3:
        return await message.reply_text("Usage:\n`/addword easy apple`\n`/addword medium computer`\n`/addword hard international`")

    difficulty = message.command[1].lower()
    new_word = clean_answer(message.command[2])

    if difficulty not in WORDS:
        return await message.reply_text("❌ Valid difficulties: `easy`, `medium`, `hard`.")

    if len(new_word) < 3:
        return await message.reply_text("❌ Word bohot chhota hai.")

    if new_word in WORDS[difficulty]:
        res = await message.reply_text("⚠️ Yeh word already database mein available hai.")
        asyncio.create_task(delete_after(message, 5))
        asyncio.create_task(delete_after(res, 5))
        return

    WORDS[difficulty].append(new_word)
    DB.execute("INSERT OR IGNORE INTO custom_words(difficulty, word) VALUES (?, ?)", (difficulty, new_word))
    DB.commit()

    res = await message.reply_text(f"✅ Word **'{new_word.upper()}'** added to **{difficulty.upper()}** bank!")
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command("delword"))
async def delword_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

    if len(message.command) < 3:
        return await message.reply_text("Usage:\n`/delword easy apple`\n`/delword medium computer`\n`/delword hard international`")

    difficulty = message.command[1].lower()
    word_to_del = clean_answer(message.command[2])

    if difficulty not in WORDS:
        return await message.reply_text("❌ Valid difficulties: `easy`, `medium`, `hard`.")

    if word_to_del not in WORDS[difficulty]:
        res = await message.reply_text(f"❌ Word **'{word_to_del.upper()}'** {difficulty.upper()} bank mein nahi mila.")
        asyncio.create_task(delete_after(message, 5))
        asyncio.create_task(delete_after(res, 5))
        return

    WORDS[difficulty].remove(word_to_del)
    DB.execute("DELETE FROM custom_words WHERE difficulty=? AND word=?", (difficulty, word_to_del))
    DB.execute("DELETE FROM used_words WHERE difficulty=? AND word=?", (difficulty, word_to_del))
    DB.commit()

    res = await message.reply_text(f"🗑️ Word **'{word_to_del.upper()}'** deleted from **{difficulty.upper()}** bank!")
    asyncio.create_task(delete_after(message, 5))
    asyncio.create_task(delete_after(res, 5))

@app.on_message(filters.command(["word", "words"]))
async def words_menu_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("❌ Aap authorized nahi hain.")

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
        "📚 **JUMBLE WORD BANK**\n\n"
        f"🟢 **Easy Words:** `{len(WORDS['easy'])}`\n"
        f"🟡 **Medium Words:** `{len(WORDS['medium'])}`\n"
        f"🔴 **Hard Words:** `{len(WORDS['hard'])}`\n\n"
        "Neeche buttons par click karke category ke words check karein (Single-tap copy supported):",
        reply_markup=kb
    )
    asyncio.create_task(delete_after(message, 3))

# ============================================================
# GAME & SETTINGS COMMANDS
# ============================================================

@app.on_message(filters.command("settings"))
async def settings_cmd(_, message: Message):
    if not await is_admin_or_owner(message.chat, message.from_user.id):
        return await message.reply_text("❌ Only group admins can configure settings.")

    s = get_settings(message.chat.id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ Stop Game", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ Start Game", callback_data="set_start_game")
    del_btn = InlineKeyboardButton("🗑️ Auto-Del: ON", callback_data="set_toggle_autodel") if s["auto_delete"] else InlineKeyboardButton("🗑️ Auto-Del: OFF", callback_data="set_toggle_autodel")

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
        f"⚙️ **Jumble Group Settings**\n\n"
        f"🟢 Game Status: **{'Running' if s['is_active'] else 'Stopped'}**\n"
        f"🗑️ Auto Delete Old: **{'Enabled' if s['auto_delete'] else 'Disabled'}**\n"
        f"🎯 Default Mode: **{str(cur_diff).title()}**\n"
        f"⏱️ Timers: Easy: **{s['easy']}s** | Medium: **{s['medium']}s** | Hard: **{s['hard']}s**\n"
        f"⭐ Reward per word: **{s['points_per_word']} pts**",
        reply_markup=kb
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
        return await message.reply_text("Usage:\n• `/jumblefight @username`\n• `/jumblefight UserID`\n• Reply to a user with `/jumblefight`")

    if target_user.id == message.from_user.id:
        return await message.reply_text("❌ Khud ke sath fight nahi kar sakte.")

    if target_user.is_bot:
        return await message.reply_text("❌ Bots ke sath match nahi ho sakta.")

    ensure_user(message.from_user)
    ensure_user(target_user)

    key = message.chat.id
    if key in JUMBLE_FIGHT:
        return await message.reply_text("⚔️ Is group mein already Jumble Fight chal rahi hai.")

    FIGHT_LOBBY[key] = {
        "p1": message.from_user.id,
        "p2": target_user.id,
        "p1_name": message.from_user.first_name,
        "p2_name": target_user.first_name,
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
            InlineKeyboardButton("🚀 START FIGHT", callback_data="f_start")
        ]
    ])

    await message.reply_text(
        f"⚔️ **JUMBLE FIGHT 1v1 MATCH SETUP**\n\n"
        f"👤 **{message.from_user.first_name}** 🆚 **{target_user.first_name}**\n\n"
        f"🎯 Mode: **Medium** | ⏱️ Round Timer: **60s**\n"
        f"Select mode & timer below, then press **START FIGHT**!",
        reply_markup=kb
    )

@app.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    total = u["fight_wins"] + u["fight_losses"]
    winrate = ((u["fight_wins"] / total) * 100) if total else 0

    await message.reply_text(
        f"👤 **{u['name']}**\n\n"
        f"⭐ Points: **{u['points']}**\n"
        f"🧩 Solved: **{u['solved']}**\n"
        f"🔥 Streak: **{u['streak']}** (Best: {u['best_streak']})\n"
        f"💡 Hints: **3 per puzzle**\n\n"
        f"⚔️ Jumble Fight: **{u['fight_wins']}W - {u['fight_losses']}L**\n"
        f"📈 Win Rate: **{winrate:.1f}%**"
    )

@app.on_message(filters.command("leaderboard"))
async def leaderboard_cmd(_, message: Message):
    rows = DB.execute("SELECT * FROM users ORDER BY points DESC LIMIT 10").fetchall()
    if not rows:
        return await message.reply_text("🏆 Leaderboard empty hai.")

    text = "🏆 **GLOBAL JUMBLE LEADERBOARD**\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, u in enumerate(rows, 1):
        medal = medals[i - 1] if i <= 3 else f"`{i}.`"
        name = u["name"][:18]
        text += f"{medal} **{name}** — ⭐ {u['points']} pts\n"

    await message.reply_text(text)

@app.on_message(filters.command("setpoints"))
async def set_points(_, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Sirf Owner points reward change kar sakta hai.")

    if len(message.command) != 2:
        return await message.reply_text("Usage: `/setpoints 20`")

    try:
        points = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid points number.")

    DB.execute("""
        INSERT INTO settings(chat_id, points_per_word)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET points_per_word=excluded.points_per_word
    """, (message.chat.id, points))
    DB.commit()

    await message.reply_text(f"✅ Is chat mein per word reward **{points} points** set kar diya.")

# ============================================================
# UNIFIED ANSWER HANDLER
# ============================================================

@app.on_message(
    filters.text &
    ~filters.command([
        "start", "help", "jumble", "stats", "leaderboard",
        "jumblefight", "fight", "rapido", "settings", "setpoints",
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

                r_msg = await message.reply_text(
                    f"⚡ **{message.from_user.first_name} WON ROUND {game['round']}!**\n"
                    f"🏆 Round Score: {game['scores'][user_id]}"
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
        pts_reward = settings["points_per_word"]

        new_streak = u["streak"] + 1
        best = max(new_streak, u["best_streak"])

        DB.execute("""
            UPDATE users
            SET points=points+?, solved=solved+1, streak=?, best_streak=?
            WHERE user_id=?
        """, (pts_reward, new_streak, best, user_id))
        DB.commit()

        if settings["auto_delete"] and game["message_id"]:
            await safe_delete_and_unpin(chat_id, game["message_id"])

        c_msg = await message.reply_text(
            f"🎉 **CORRECT!**\n\n"
            f"👤 {message.from_user.first_name}\n"
            f"✅ Answer: **{game['word'].upper()}**\n"
            f"⭐ **+{pts_reward} points**\n"
            f"🔥 Current Streak: **{new_streak}**\n\n"
            f"🔄 Next puzzle coming in 3 seconds..."
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

        hint_row = DB.execute("SELECT * FROM puzzle_hints WHERE chat_id=? AND puzzle_id=? AND user_id=?", (chat_id, puzzle_id, user_id)).fetchone()
        hints_used = hint_row["hints_used"] if hint_row else 0
        revealed_indices = [int(i) for i in hint_row["revealed_indices"].split(",") if i] if hint_row else []

        if hints_used >= 3:
            return await query.answer("❌ Is word ke liye 3 hints complete ho chuki hain!", show_alert=True)

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
        return await query.answer(f"💡 Hint: Letter #{chosen_index + 1} is '{letter}'\nRemaining: {3 - hints_used}/3", show_alert=True)

    elif data == "fight_hint":
        game = JUMBLE_FIGHT.get(chat_id)
        if not game or user_id not in game["players"]:
            return await query.answer("❌ Sirf match players hints le sakte hain.", show_alert=True)

        p_hint = game["round_hints"][user_id]
        if p_hint["count"] >= 3:
            return await query.answer("❌ Is round ke 3 hints use ho chuke hain!", show_alert=True)

        word = game["word"]
        avail = [i for i in range(len(word)) if i not in p_hint["indices"]]
        if not avail:
            return await query.answer("❌ Aur letters reveal nahi ho sakte.", show_alert=True)

        idx = random.choice(avail)
        p_hint["indices"].append(idx)
        p_hint["count"] += 1

        return await query.answer(f"💡 Hint: Letter #{idx + 1} is '{word[idx].upper()}'\nRemaining: {3 - p_hint['count']}/3", show_alert=True)

    # ============================================================
    # WORD BANK PAGINATED VIEWER (WITH SINGLE TAP COPY)
    # ============================================================
    elif data.startswith("wb_"):
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

        # Format words for easy single-tap copy
        formatted_list = "  •  ".join(f"`{w.upper()}`" for w in page_words)

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
            f"📚 **{diff.upper()} WORDS BANK** (Total: `{total_words}`)\n"
            f"📌 *Tip: Tap on any word below to copy it!*\n\n"
            f"{formatted_list}\n\n"
            f"➕ Add: `/addword {diff} <word>`\n"
            f"➖ Del: `/delword {diff} <word>`"
        )

        await query.answer()
        try:
            await query.message.edit_text(msg, reply_markup=kb)
        except MessageNotModified:
            pass
        except Exception:
            try:
                await app.send_message(chat_id, msg, reply_markup=kb)
            except Exception:
                pass

    elif data == "noop_page":
        await query.answer("Current Page Number", show_alert=False)

    elif data == "back_to_words_menu":
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
        await query.answer()
        try:
            await query.message.edit_text(
                "📚 **JUMBLE WORD BANK**\n\n"
                f"🟢 **Easy Words:** `{len(WORDS['easy'])}`\n"
                f"🟡 **Medium Words:** `{len(WORDS['medium'])}`\n"
                f"🔴 **Hard Words:** `{len(WORDS['hard'])}`\n\n"
                "Neeche buttons par click karke category ke words check karein (Single-tap copy supported):",
                reply_markup=kb
            )
        except Exception:
            pass

    elif data.startswith("f_"):
        lobby = FIGHT_LOBBY.get(chat_id)
        if not lobby:
            return await query.answer("Match lobby expire ho chuki hai.", show_alert=True)

        if user_id not in (lobby["p1"], lobby["p2"]) and not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Match players hi settings change kar sakte hain.", show_alert=True)

        if data.startswith("f_diff_"):
            lobby["difficulty"] = data.split("_")[2]
            await query.answer(f"Difficulty set to {lobby['difficulty'].upper()}")
        elif data.startswith("f_time_"):
            lobby["timer"] = int(data.split("_")[2])
            await query.answer(f"Timer set to {lobby['timer']}s")
        elif data == "f_start":
            JUMBLE_FIGHT[chat_id] = {
                "players": [lobby["p1"], lobby["p2"]],
                "names": {lobby["p1"]: lobby["p1_name"], lobby["p2"]: lobby["p2_name"]},
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
            await query.answer("🚀 Starting Jumble Fight!")
            asyncio.create_task(fight_next(chat_id))
            return

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
                InlineKeyboardButton("🚀 START FIGHT", callback_data="f_start")
            ]
        ])
        try:
            await query.message.edit_text(
                f"⚔️ **JUMBLE FIGHT 1v1 MATCH SETUP**\n\n"
                f"👤 **{lobby['p1_name']}** 🆚 **{lobby['p2_name']}**\n\n"
                f"🎯 Mode: **{lobby['difficulty'].title()}** | ⏱️ Round Timer: **{lobby['timer']}s**\n"
                f"Press **START FIGHT** to begin!",
                reply_markup=kb
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
                await query.message.edit_text("🎯 **Default Jumble Difficulty Chuno:**", reply_markup=kb)
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
                await query.message.edit_text("⏱️ **Select Timer Duration:**", reply_markup=kb)
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

        sk_msg = await query.message.reply_text(f"⏭️ **Skipped!**\nAnswer: **{game['word'].upper()}**\n\n🔄 Next puzzle starting in 3 seconds...")
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
        f"⚙️ **Jumble Group Settings**\n\n"
        f"🟢 Game Status: **{'Running' if s['is_active'] else 'Stopped'}**\n"
        f"🗑️ Auto Delete Old: **{'Enabled' if s['auto_delete'] else 'Disabled'}**\n"
        f"🎯 Default Mode: **{str(cur_diff).title()}**\n"
        f"⏱️ Timers: Easy: **{s['easy']}s** | Medium: **{s['medium']}s** | Hard: **{s['hard']}s**\n"
        f"⭐ Reward per word: **{s['points_per_word']} pts**"
    )
    try:
        await message_obj.edit_text(text, reply_markup=kb)
    except MessageNotModified:
        pass

# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    print("🚀 Jumble Fight & Jumble Bot Started Successfully!")
    app.run()
