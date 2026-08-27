import asyncio
import io
import os
import random
import sqlite3
import time
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.getenv("API_ID", "35218869"))
API_HASH = os.getenv("API_HASH", "80baadcfd00a39a0ff1f5f529d23156f")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "8564072723"))

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

EASY = """
apple banana orange mango table chair house water school friend family
happy garden flower animal window bottle mobile computer summer winter
river music movie player football cricket doctor teacher market village
country morning evening coffee bread pizza camera phone pencil paper
train bus road car earth world light night star cloud rain green blue
black white tiger lion horse rabbit monkey fish bird tree fruit
""".split()

MEDIUM = """
adventure beautiful knowledge education important dangerous different
experience friendship happiness technology information internet
mountain waterfall sunshine keyboard hospital university restaurant
football cricket championship tournament engineer scientist medicine
history geography language computer network application database
security password community discussion entertainment television
photography creativity imagination discovery opportunity challenge
journey traveler vacation airport railway newspaper magazine
""".split()

HARD = """
extraordinary responsibility communication determination independence
international transformation understanding environment intelligence
architecture investigation recommendation administration opportunity
entrepreneurship cryptocurrency cybersecurity authentication
programming mathematics biotechnology astrophysics psychology
philosophy civilization transportation infrastructure globalization
misunderstanding pronunciation encyclopedia experimentation
electromagnetism thermodynamics interoperability decentralization
""".split()

WORDS = {
    "easy": list(set(w.lower() for w in EASY if len(w) >= 4)),
    "medium": list(set(w.lower() for w in MEDIUM if len(w) >= 6)),
    "hard": list(set(w.lower() for w in HARD if len(w) >= 8))
}

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
    rapido_wins INTEGER DEFAULT 0,
    rapido_losses INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY,
    easy INTEGER DEFAULT 120,
    medium INTEGER DEFAULT 300,
    hard INTEGER DEFAULT 600,
    points_per_word INTEGER DEFAULT 10,
    default_diff TEXT DEFAULT 'medium',
    is_active INTEGER DEFAULT 1
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
    DB.commit()

run_migrations()

# ============================================================
# HELPERS
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

def get_settings(chat_id):
    row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        DB.execute("""
            INSERT INTO settings(chat_id, easy, medium, hard, points_per_word, default_diff, is_active)
            VALUES (?, 120, 300, 600, 10, 'medium', 1)
            ON CONFLICT(chat_id) DO NOTHING
        """, (chat_id,))
        DB.commit()
        row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    return row

def is_owner(user_id):
    return user_id == OWNER_ID

async def is_admin_or_owner(chat, user_id):
    if user_id == OWNER_ID:
        return True
    if chat.type in (ChatType.PRIVATE,):
        return True
    try:
        member = await chat.get_member(user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False

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
    if chat_id in RAPIDO:
        return

    settings = get_settings(chat_id)
    if not settings["is_active"]:
        return

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
    if chat_id in RAPIDO:
        return

    row = DB.execute("SELECT * FROM games WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id)).fetchone()
    if not row or row["solved"]:
        return

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    try:
        await app.send_message(
            chat_id,
            f"⏰ **Time's Up!**\n\n❌ Nobody solved it.\n✅ Answer: **{row['word'].upper()}**\n\n🔄 Next puzzle starting in 3 seconds..."
        )
    except Exception:
        pass

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if chat_id not in RAPIDO and s["is_active"]:
        await start_game(chat_id, difficulty, chat_id)

# ============================================================
# RAPIDO 1v1 SYSTEM
# ============================================================

RAPIDO = {}
RAPIDO_LOBBY = {}

def rapido_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 Hint (3/word)", callback_data="rapido_hint")
        ]
    ])

async def rapido_timeout_task(chat_id, round_num):
    timer_duration = RAPIDO.get(chat_id, {}).get("timer", 60)
    await asyncio.sleep(timer_duration)
    async with LOCK:
        game = RAPIDO.get(chat_id)
        if not game or game["round"] != round_num:
            return

        word = game["word"]
        await app.send_message(
            chat_id,
            f"⏰ **Round {round_num} Timeout!**\n❌ Kisi ne solve nahi kiya.\n✅ Answer: **{word.upper()}**\n\n🔄 Next round starting..."
        )
    
    await asyncio.sleep(3)
    await rapido_next(chat_id)

async def rapido_next(chat_id):
    game = RAPIDO.get(chat_id)
    if not game:
        return

    if game.get("task") and not game["task"].done():
        game["task"].cancel()

    game["round"] += 1
    if game["round"] > 10:
        await finish_rapido(chat_id)
        return

    diff = game["difficulty"]
    word = random.choice(WORDS[diff])
    jumbled = jumble_word(word)

    game["word"] = word
    game["expires"] = time.time() + game["timer"]
    game["round_hints"] = defaultdict(lambda: {"count": 0, "indices": []})

    image = make_puzzle_image(jumbled, f"RAPIDO {diff.upper()}", game["round"])
    
    sent = await app.send_photo(
        chat_id,
        photo=image,
        caption=(
            f"⚔️ **RAPIDO — ROUND {game['round']}/10**\n\n"
            f"🎯 Difficulty: **{diff.title()}**\n"
            f"⏱️ Time: **{game['timer']}s**\n"
            f"🔀 Solve fastest!\n"
            f"👥 Players: {game['names'][game['players'][0]]} 🆚 {game['names'][game['players'][1]]}"
        ),
        reply_markup=rapido_keyboard()
    )

    try:
        await sent.pin(disable_notification=True)
    except Exception:
        pass

    game["task"] = asyncio.create_task(rapido_timeout_task(chat_id, game["round"]))

async def finish_rapido(chat_id):
    game = RAPIDO.pop(chat_id, None)
    if not game:
        return

    if game.get("task") and not game["task"].done():
        game["task"].cancel()

    p1, p2 = game["players"]
    s1, s2 = game["scores"][p1], game["scores"][p2]

    if s1 > s2:
        winner, loser = p1, p2
    elif s2 > s1:
        winner, loser = p2, p1
    else:
        winner = loser = None

    if winner:
        DB.execute("UPDATE users SET rapido_wins=rapido_wins+1 WHERE user_id=?", (winner,))
        DB.execute("UPDATE users SET rapido_losses=rapido_losses+1 WHERE user_id=?", (loser,))
        DB.commit()

    n1, n2 = game["names"][p1], game["names"][p2]
    result = f"🏁 **RAPIDO MATCH OVER!**\n\n👤 **{n1}** — {s1} pts\n👤 **{n2}** — {s2} pts\n\n"

    if winner:
        result += f"🏆 Match Winner: **{game['names'][winner]}** 🎉"
    else:
        result += "🤝 **Match Draw!**"

    await app.send_message(chat_id, result)

# ============================================================
# COMMANDS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    ensure_user(message.from_user)
    text = (
        "🧩 **Welcome to Advanced Jumble Bot!**\n\n"
        "🎮 **Game Commands:**\n"
        "• `/jumble` — Start Auto-loop Jumble Game\n"
        "• `/rapido @user` — 1v1 Battle with Custom Timer & Mode\n"
        "• `/settings` — Admin Panel (Start/Stop, Mode & Timer)\n\n"
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
    await message.reply_text(
        "🧩 **Jumble Commands Guide**\n\n"
        "`/jumble` — Start auto-looping jumble game\n"
        "`/rapido @user` — 1v1 battle match\n"
        "`/settings` — Admin start/stop and game settings\n"
        "`/leaderboard` — Top players ranking\n"
        "`/stats` — Personal score card\n\n"
        "💡 Har word par aapko **3 fresh hints** milti hain.\n"
        "👑 **Owner Commands:**\n"
        "`/addword easy word` — Add new word to bank\n"
        "`/setpoints 20` — Set per-word reward"
    )

@app.on_message(filters.command("addword"))
async def addword_cmd(_, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Owner only command.")

    if len(message.command) < 3:
        return await message.reply_text("Usage:\n`/addword easy word`\n`/addword medium word`\n`/addword hard word`")

    difficulty = message.command[1].lower()
    new_word = clean_answer(message.command[2])

    if difficulty not in WORDS:
        return await message.reply_text("❌ Category must be `easy`, `medium`, or `hard`.")

    if len(new_word) < 3:
        return await message.reply_text("❌ Word bohot chhota hai.")

    if new_word in WORDS[difficulty]:
        return await message.reply_text("⚠️ Yeh word already bank me exist karta hai.")

    WORDS[difficulty].append(new_word)
    await message.reply_text(f"✅ Word **'{new_word.upper()}'** added to **{difficulty.upper()}** bank!")

@app.on_message(filters.command("settings"))
async def settings_cmd(_, message: Message):
    if not await is_admin_or_owner(message.chat, message.from_user.id):
        return await message.reply_text("❌ Only group admins can configure settings.")

    s = get_settings(message.chat.id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ Stop Game", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ Start Game", callback_data="set_start_game")
    
    kb = InlineKeyboardMarkup([
        [
            status_btn,
            InlineKeyboardButton(f"Mode: {str(cur_diff).upper()}", callback_data="set_menu_mode")
        ],
        [
            InlineKeyboardButton("⏱️ Timers", callback_data="set_menu_timers"),
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
        ]
    ])
    await message.reply_text(
        f"⚙️ **Jumble Group Settings**\n\n"
        f"🟢 Game Status: **{'Running' if s['is_active'] else 'Stopped'}**\n"
        f"🎯 Default Mode: **{str(cur_diff).title()}**\n"
        f"⏱️ Timers: Easy: **{s['easy']}s** | Medium: **{s['medium']}s** | Hard: **{s['hard']}s**\n"
        f"⭐ Reward per word: **{s['points_per_word']} pts**",
        reply_markup=kb
    )

@app.on_message(filters.command("jumble"))
async def jumble_cmd(_, message: Message):
    ensure_user(message.from_user)
    if message.chat.id in RAPIDO:
        return await message.reply_text("⚔️ Rapido battle chal rahi hai, game khatam hone tak wait karein.")

    DB.execute("UPDATE settings SET is_active=1 WHERE chat_id=?", (message.chat.id,))
    DB.commit()

    s = get_settings(message.chat.id)
    default_d = s["default_diff"] if "default_diff" in s.keys() else "medium"
    difficulty = message.command[1].lower() if len(message.command) > 1 else default_d
    
    if difficulty not in WORDS:
        difficulty = "medium"

    await start_game(message.chat.id, difficulty, message)

@app.on_message(filters.command("rapido"))
async def rapido_cmd(_, message: Message):
    if not is_group(message):
        return await message.reply_text("❌ Rapido sirf groups mein chalta hai.")

    target_user = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        try:
            target_user = await app.get_users(message.command[1])
        except Exception:
            return await message.reply_text("❌ User nahi mila.")
    else:
        return await message.reply_text("Usage:\n• `/rapido @username`\n• Reply to a user with `/rapido`")

    if target_user.id == message.from_user.id:
        return await message.reply_text("❌ Khud ko challenge nahi kar sakte.")

    if target_user.is_bot:
        return await message.reply_text("❌ Bots ke sath match nahi ho sakta.")

    ensure_user(message.from_user)
    ensure_user(target_user)

    key = message.chat.id
    if key in RAPIDO:
        return await message.reply_text("⚔️ Is group mein already Rapido battle chal rahi hai.")

    RAPIDO_LOBBY[key] = {
        "p1": message.from_user.id,
        "p2": target_user.id,
        "p1_name": message.from_user.first_name,
        "p2_name": target_user.first_name,
        "difficulty": "medium",
        "timer": 60
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Easy", callback_data="r_diff_easy"),
            InlineKeyboardButton("🟡 Medium", callback_data="r_diff_medium"),
            InlineKeyboardButton("🔴 Hard", callback_data="r_diff_hard")
        ],
        [
            InlineKeyboardButton("⏱️ 30s", callback_data="r_time_30"),
            InlineKeyboardButton("⏱️ 45s", callback_data="r_time_45"),
            InlineKeyboardButton("⏱️ 60s", callback_data="r_time_60")
        ],
        [
            InlineKeyboardButton("🚀 START BATTLE", callback_data="r_start")
        ]
    ])

    await message.reply_text(
        f"⚔️ **RAPIDO 1v1 MATCH SETUP**\n\n"
        f"👤 **{message.from_user.first_name}** 🆚 **{target_user.first_name}**\n\n"
        f"🎯 Mode: **Medium** | ⏱️ Round Timer: **60s**\n"
        f"Select mode & timer below, then press **START BATTLE**!",
        reply_markup=kb
    )

@app.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    total = u["rapido_wins"] + u["rapido_losses"]
    winrate = ((u["rapido_wins"] / total) * 100) if total else 0

    await message.reply_text(
        f"👤 **{u['name']}**\n\n"
        f"⭐ Points: **{u['points']}**\n"
        f"🧩 Solved: **{u['solved']}**\n"
        f"🔥 Streak: **{u['streak']}** (Best: {u['best_streak']})\n"
        f"💡 Hints: **3 per puzzle**\n\n"
        f"⚔️ Rapido Record: **{u['rapido_wins']}W - {u['rapido_losses']}L**\n"
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
        return await message.reply_text("❌ Owner only command.")

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
        "rapido", "settings", "setpoints", "addword"
    ])
)
async def unified_answer_handler(_, message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    cleaned_input = clean_answer(message.text)

    # 1. Active Rapido Check
    if chat_id in RAPIDO:
        async with LOCK:
            game = RAPIDO.get(chat_id)
            if not game or user_id not in game["players"]:
                return

            if time.time() <= game["expires"] and cleaned_input == clean_answer(game["word"]):
                if game.get("task") and not game["task"].done():
                    game["task"].cancel()

                game["scores"][user_id] += 1
                await message.reply_text(
                    f"⚡ **{message.from_user.first_name} WON ROUND {game['round']}!**\n"
                    f"🏆 Round Score: {game['scores'][user_id]}"
                )
                await asyncio.sleep(2.5)
                await rapido_next(chat_id)
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

        await message.reply_text(
            f"🎉 **CORRECT!**\n\n"
            f"👤 {message.from_user.first_name}\n"
            f"✅ Answer: **{game['word'].upper()}**\n"
            f"⭐ **+{pts_reward} points**\n"
            f"🔥 Current Streak: **{new_streak}**\n\n"
            f"🔄 Next puzzle coming in 3 seconds..."
        )

        await asyncio.sleep(3)
        s = get_settings(chat_id)
        if chat_id not in RAPIDO and s["is_active"]:
            await start_game(chat_id, game["difficulty"], chat_id)

# ============================================================
# CALLBACK QUERIES
# ============================================================

@app.on_callback_query()
async def callback_router(_, query: CallbackQuery):
    data = query.data
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    # Normal Hint
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

    # Rapido Hint
    elif data == "rapido_hint":
        game = RAPIDO.get(chat_id)
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

    # Rapido Setup Selection
    elif data.startswith("r_"):
        lobby = RAPIDO_LOBBY.get(chat_id)
        if not lobby:
            return await query.answer("Match lobby expire ho chuki hai.", show_alert=True)

        if user_id not in (lobby["p1"], lobby["p2"]) and not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Match creator ya player hi setting change kar sakte hain.", show_alert=True)

        if data.startswith("r_diff_"):
            lobby["difficulty"] = data.split("_")[2]
            await query.answer(f"Difficulty set to {lobby['difficulty'].upper()}")
        elif data.startswith("r_time_"):
            lobby["timer"] = int(data.split("_")[2])
            await query.answer(f"Timer set to {lobby['timer']}s")
        elif data == "r_start":
            RAPIDO[chat_id] = {
                "players": [lobby["p1"], lobby["p2"]],
                "names": {lobby["p1"]: lobby["p1_name"], lobby["p2"]: lobby["p2_name"]},
                "round": 0,
                "scores": defaultdict(int),
                "word": None,
                "expires": None,
                "task": None,
                "difficulty": lobby["difficulty"],
                "timer": lobby["timer"]
            }
            del RAPIDO_LOBBY[chat_id]
            await query.message.delete()
            await query.answer("🚀 Starting Match!")
            await rapido_next(chat_id)
            return

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='easy' else ''}Easy", callback_data="r_diff_easy"),
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='medium' else ''}Medium", callback_data="r_diff_medium"),
                InlineKeyboardButton(f"{'✅ ' if lobby['difficulty']=='hard' else ''}Hard", callback_data="r_diff_hard")
            ],
            [
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==30 else ''}30s", callback_data="r_time_30"),
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==45 else ''}45s", callback_data="r_time_45"),
                InlineKeyboardButton(f"{'✅ ' if lobby['timer']==60 else ''}60s", callback_data="r_time_60")
            ],
            [
                InlineKeyboardButton("🚀 START BATTLE", callback_data="r_start")
            ]
        ])
        try:
            await query.message.edit_text(
                f"⚔️ **RAPIDO 1v1 MATCH SETUP**\n\n"
                f"👤 **{lobby['p1_name']}** 🆚 **{lobby['p2_name']}**\n\n"
                f"🎯 Mode: **{lobby['difficulty'].title()}** | ⏱️ Round Timer: **{lobby['timer']}s**\n"
                f"Press **START BATTLE** to begin!",
                reply_markup=kb
            )
        except MessageNotModified:
            pass

    # Admin Settings Menus
    elif data.startswith("set_"):
        if not await is_admin_or_owner(query.message.chat, user_id):
            return await query.answer("❌ Only admins can change settings.", show_alert=True)

        if data == "set_start_game":
            DB.execute("UPDATE settings SET is_active=1 WHERE chat_id=?", (chat_id,))
            DB.commit()
            await query.answer("▶️ Game started!")
            await show_settings_panel(query.message, chat_id)
            s = get_settings(chat_id)
            await start_game(chat_id, s["default_diff"], query.message)

        elif data == "set_stop_game":
            DB.execute("UPDATE settings SET is_active=0 WHERE chat_id=?", (chat_id,))
            DB.execute("DELETE FROM games WHERE chat_id=?", (chat_id,))
            DB.commit()
            await query.answer("⏹️ Game stopped!")
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
        await query.message.reply_text(f"⏭️ **Skipped!**\nAnswer: **{game['word'].upper()}**\n\n🔄 Next puzzle starting in 3 seconds...")
        await query.answer("Skipped.")
        await asyncio.sleep(3)
        s = get_settings(chat_id)
        if s["is_active"]:
            await start_game(chat_id, game["difficulty"], chat_id)

    elif data == "newword":
        old = DB.execute("SELECT * FROM games WHERE chat_id=?", (chat_id,)).fetchone()
        if old and not old["solved"] and time.time() <= old["expires"]:
            return await query.answer("❌ Current puzzle abhi active hai.", show_alert=True)

        s = get_settings(chat_id)
        difficulty = old["difficulty"] if old else s["default_diff"]
        await query.answer("🧩 Starting new puzzle...")
        await start_game(chat_id, difficulty, query.message)

    elif data == "close_panel":
        await query.message.delete()

async def show_settings_panel(message_obj, chat_id):
    s = get_settings(chat_id)
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ Stop Game", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ Start Game", callback_data="set_start_game")
    
    kb = InlineKeyboardMarkup([
        [
            status_btn,
            InlineKeyboardButton(f"Mode: {str(cur_diff).upper()}", callback_data="set_menu_mode")
        ],
        [
            InlineKeyboardButton("⏱️ Timers", callback_data="set_menu_timers"),
            InlineKeyboardButton("❌ Close", callback_data="close_panel")
        ]
    ])
    text = (
        f"⚙️ **Jumble Group Settings**\n\n"
        f"🟢 Game Status: **{'Running' if s['is_active'] else 'Stopped'}**\n"
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
    print("🚀 Jumble & Rapido Bot Started Successfully!")
    app.run()
