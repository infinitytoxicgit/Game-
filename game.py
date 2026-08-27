import asyncio
import io
import os
import random
import sqlite3
import time
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.getenv("API_ID", "35218869"))
API_HASH = os.getenv("API_HASH", "80baadcfd00a39a0ff1f5f529d23156f")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "8564072723"))

START_IMG = "https://graph.org/file/7c0c03d68308f0c5dad42-ddb933df03f0ff0632.jpg"

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
# AUTOMATIC WORD BANK
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
# DATABASE SETUP
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
    points_per_word INTEGER DEFAULT 10
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
            INSERT INTO settings(chat_id, easy, medium, hard, points_per_word)
            VALUES (?, 120, 300, 600, 10)
            ON CONFLICT(chat_id) DO NOTHING
        """, (chat_id,))
        DB.commit()
        row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    return row

def is_owner(user_id):
    return user_id == OWNER_ID

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

def make_puzzle_image(jumbled, difficulty, puzzle_id):
    img = Image.new("RGB", (1200, 650), "#10131a")
    draw = ImageDraw.Draw(img)

    title_font = get_font(55)
    small_font = get_font(35)

    # Dynamic Font Calculation to prevent text cutoff
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
    draw.text((600, 480), f"{difficulty.upper()}  •  PUZZLE #{puzzle_id}", anchor="mm", font=small_font, fill="#ffffff")
    draw.text((600, 545), "Unscramble the letters!", anchor="mm", font=small_font, fill="#aaaaaa")

    bio = io.BytesIO()
    bio.name = "puzzle.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

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

def new_game_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆕 New Word", callback_data="newword")
        ]
    ])

# ============================================================
# NORMAL GAME CORE (DM + GROUP)
# ============================================================

async def start_game(chat_id, difficulty, message):
    settings = get_settings(chat_id)
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
    sent = await message.reply_photo(
        image,
        caption=(
            f"🧩 **Jumble #{puzzle_id}**\n\n"
            f"🎯 Difficulty: **{difficulty.title()}**\n"
            f"⏱️ Time: **{timer_val // 60} min {timer_val % 60} sec**\n"
            f"⭐ Reward: **+{settings['points_per_word']} Points**\n\n"
            f"🔀 Unscramble the letters!\n"
            f"💬 Simply type the answer in chat."
        ),
        reply_markup=normal_keyboard()
    )

    DB.execute("UPDATE games SET message_id=? WHERE chat_id=?", (sent.id, chat_id))
    DB.commit()

    try:
        await sent.pin(disable_notification=True)
    except Exception:
        pass

    asyncio.create_task(expire_game(chat_id, puzzle_id, expires))

async def expire_game(chat_id, puzzle_id, expires):
    await asyncio.sleep(max(0, expires - time.time()))
    row = DB.execute("SELECT * FROM games WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id)).fetchone()
    if not row or row["solved"]:
        return

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    try:
        await app.send_message(
            chat_id,
            f"⏰ **Time's Up!**\n\n❌ Nobody solved it.\n✅ Answer: **{row['word'].upper()}**",
            reply_markup=new_game_keyboard()
        )
    except Exception:
        pass

# ============================================================
# RAPIDO 1v1 SYSTEM
# ============================================================

RAPIDO = {}

async def rapido_timeout_task(chat_id, round_num):
    await asyncio.sleep(60)
    async with LOCK:
        game = RAPIDO.get(chat_id)
        if not game or game["round"] != round_num:
            return

        word = game["word"]
        await app.send_message(
            chat_id,
            f"⏰ **Round {round_num} Timeout!**\n❌ Kisi ne solve nahi kiya.\n✅ Answer: **{word.upper()}**"
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

    diff = random.choice(["easy", "medium", "hard"])
    word = random.choice(WORDS[diff])
    jumbled = jumble_word(word)

    game["word"] = word
    game["expires"] = time.time() + 60
    game["task"] = asyncio.create_task(rapido_timeout_task(chat_id, game["round"]))

    image = make_puzzle_image(jumbled, f"RAPIDO {diff.upper()}", game["round"])
    await app.send_photo(
        chat_id,
        image,
        caption=(
            f"⚔️ **RAPIDO — ROUND {game['round']}/10**\n\n"
            f"🔀 Solve fastest!\n"
            f"⏱️ 60 seconds"
        )
    )

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
    result = f"🏁 **RAPIDO FINISHED!**\n\n👤 **{n1}** — {s1} pts\n👤 **{n2}** — {s2} pts\n\n"

    if winner:
        result += f"🏆 Winner: **{game['names'][winner]}** 🎉"
    else:
        result += "🤝 **Match Draw!**"

    await app.send_message(chat_id, result)

# ============================================================
# COMMANDS
# ============================================================

@app.on_message(filters.command("start"))
async def start_cmd(_, message):
    ensure_user(message.from_user)
    text = (
        "🧩 **Welcome to Advanced Jumble Bot!**\n\n"
        "🎮 **Game Commands:**\n"
        "• `/jumble easy` — 2 Mins Timer\n"
        "• `/jumble medium` — 5 Mins Timer\n"
        "• `/jumble hard` — 10 Mins Timer\n\n"
        "⚔️ **1v1 Rapido (Group Only):**\n"
        "• `/rapido @username` or Reply `/rapido`\n\n"
        "📊 **Stats & Rankings:**\n"
        "• `/stats` — Your Game Performance\n"
        "• `/leaderboard` — Top Global Players\n"
        "• `/help` — Full Guide"
    )

    if message.chat.type == ChatType.PRIVATE or str(message.chat.type).lower() in ("private", "chattype.private"):
        try:
            await message.reply_photo(photo=START_IMG, caption=text)
        except Exception:
            await message.reply_text(text)
    else:
        await message.reply_text(text)

@app.on_message(filters.command("help"))
async def help_cmd(_, message):
    await message.reply_text(
        "🧩 **Jumble Commands Guide**\n\n"
        "`/jumble easy` — 2 mins timer\n"
        "`/jumble medium` — 5 mins timer\n"
        "`/jumble hard` — 10 mins timer\n\n"
        "`/rapido @user` — 10-word fast 1v1 battle (Group only)\n"
        "`/leaderboard` — Top players ranking\n"
        "`/stats` — Personal score card\n\n"
        "💡 Har word par aapko **3 fresh hints** milti hain.\n"
        "👑 **Owner Commands:**\n"
        "`/addword easy word` — Add new word\n"
        "`/settimer easy 120` — Set timer\n"
        "`/setpoints 20` — Set points"
    )

@app.on_message(filters.command("addword"))
async def addword_cmd(_, message):
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
        return await message.reply_text("⚠️ Yeh word pehle se word bank mein exist karta hai.")

    WORDS[difficulty].append(new_word)
    await message.reply_text(f"✅ Word **'{new_word.upper()}'** successfully added to **{difficulty.upper()}** bank!")

@app.on_message(filters.command("jumble"))
async def jumble_cmd(_, message):
    ensure_user(message.from_user)
    difficulty = message.command[1].lower() if len(message.command) > 1 else "medium"
    
    if difficulty not in WORDS:
        return await message.reply_text("❌ Valid difficulties: `easy`, `medium`, `hard`")

    await start_game(message.chat.id, difficulty, message)

@app.on_message(filters.command("rapido"))
async def rapido_cmd(_, message):
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
        return await message.reply_text("❌ Bots ke sath nahi khel sakte.")

    ensure_user(message.from_user)
    ensure_user(target_user)

    key = message.chat.id
    if key in RAPIDO:
        return await message.reply_text("⚔️ Is group mein already Rapido chal raha hai.")

    RAPIDO[key] = {
        "players": [message.from_user.id, target_user.id],
        "names": {
            message.from_user.id: message.from_user.first_name,
            target_user.id: target_user.first_name
        },
        "round": 0,
        "scores": defaultdict(int),
        "word": None,
        "expires": None,
        "task": None
    }

    await message.reply_text(
        f"⚔️ **RAPIDO CHALLENGE ACCEPTED!**\n\n"
        f"👤 {message.from_user.first_name} 🆚 {target_user.first_name}\n\n"
        f"🏁 Total: **10 Rounds** (60s each)\n"
        f"⚡ Sabse pehle solve karne wale ko point milega.\n\n"
        f"Starting Round 1..."
    )
    await asyncio.sleep(2)
    await rapido_next(key)

@app.on_message(filters.command("stats"))
async def stats_cmd(_, message):
    ensure_user(message.from_user)
    u = get_user(message.from_user.id)
    total = u["rapido_wins"] + u["rapido_losses"]
    winrate = ((u["rapido_wins"] / total) * 100) if total else 0

    await message.reply_text(
        f"👤 **{u['name']}**\n\n"
        f"⭐ Points: **{u['points']}**\n"
        f"🧩 Solved: **{u['solved']}**\n"
        f"🔥 Current Streak: **{u['streak']}**\n"
        f"🏅 Best Streak: **{u['best_streak']}**\n"
        f"💡 Hints: **3 per puzzle**\n\n"
        f"⚔️ Rapido Record: **{u['rapido_wins']}W - {u['rapido_losses']}L**\n"
        f"📈 Win Rate: **{winrate:.1f}%**"
    )

@app.on_message(filters.command("leaderboard"))
async def leaderboard_cmd(_, message):
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

@app.on_message(filters.command("settimer"))
async def set_timer(_, message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Owner only command.")

    if len(message.command) != 3:
        return await message.reply_text("Usage:\n`/settimer easy 120`\n`/settimer medium 300`\n`/settimer hard 600`")

    difficulty = message.command[1].lower()
    try:
        seconds = int(message.command[2])
    except ValueError:
        return await message.reply_text("❌ Invalid seconds.")

    if difficulty not in ("easy", "medium", "hard"):
        return await message.reply_text("❌ Choose: `easy`, `medium`, `hard`")

    if not (10 <= seconds <= 3600):
        return await message.reply_text("❌ 10 se 3600 seconds ke beech rakhein.")

    get_settings(message.chat.id)
    DB.execute(f"""
        INSERT INTO settings(chat_id, {difficulty})
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET {difficulty}=excluded.{difficulty}
    """, (message.chat.id, seconds))
    DB.commit()

    await message.reply_text(f"✅ {difficulty.title()} timer is chat ke liye **{seconds}s** set ho gaya.")

@app.on_message(filters.command("setpoints"))
async def set_points(_, message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("❌ Owner only command.")

    if len(message.command) != 2:
        return await message.reply_text("Usage: `/setpoints 20`")

    try:
        points = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ Invalid points number.")

    if not (1 <= points <= 1000):
        return await message.reply_text("❌ 1 se 1000 ke beech points rakhein.")

    get_settings(message.chat.id)
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
        "rapido", "settimer", "setpoints", "addword"
    ])
)
async def unified_answer_handler(_, message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    cleaned_input = clean_answer(message.text)

    # 1. Rapido Check
    if chat_id in RAPIDO:
        async with LOCK:
            game = RAPIDO.get(chat_id)
            if game and user_id in game["players"]:
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

    # 2. Normal Game Check (DM + Group)
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
            f"🔥 Current Streak: **{new_streak}**",
            reply_markup=new_game_keyboard()
        )

# ============================================================
# CALLBACK QUERIES
# ============================================================

@app.on_callback_query(filters.regex("^hint$"))
async def hint_callback(_, query):
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game:
        return await query.answer("Koi active puzzle nahi hai.", show_alert=True)

    ensure_user(query.from_user)
    puzzle_id = game["puzzle_id"]
    word = game["word"]

    hint_row = DB.execute("""
        SELECT * FROM puzzle_hints
        WHERE chat_id=? AND puzzle_id=? AND user_id=?
    """, (chat_id, puzzle_id, user_id)).fetchone()

    hints_used = hint_row["hints_used"] if hint_row else 0
    revealed_indices = [int(i) for i in hint_row["revealed_indices"].split(",") if i] if hint_row else []

    if hints_used >= 3:
        return await query.answer("❌ Is word ke liye aapki 3 hints complete ho chuki hain!", show_alert=True)

    available_indices = [i for i in range(len(word)) if i not in revealed_indices]
    if not available_indices:
        return await query.answer("❌ Ab aur hints available nahi hain.", show_alert=True)

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
    rem = 3 - hints_used
    await query.answer(
        f"💡 Hint: Letter #{chosen_index + 1} is '{letter}'\nHints remaining for this word: {rem}/3",
        show_alert=True
    )

@app.on_callback_query(filters.regex("^skip$"))
async def skip_callback(_, query):
    if not is_owner(query.from_user.id):
        return await query.answer("❌ Sirf bot owner skip kar sakta hai.", show_alert=True)

    chat_id = query.message.chat.id
    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game:
        return await query.answer("Active game nahi mila.", show_alert=True)

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    await query.message.reply_text(
        f"⏭️ **Puzzle Skipped!**\n✅ Answer: **{game['word'].upper()}**",
        reply_markup=new_game_keyboard()
    )
    await query.answer("Skipped.")

@app.on_callback_query(filters.regex("^newword$"))
async def newword_callback(_, query):
    chat_id = query.message.chat.id
    old = DB.execute("SELECT * FROM games WHERE chat_id=?", (chat_id,)).fetchone()

    if old and not old["solved"] and time.time() <= old["expires"]:
        return await query.answer("❌ Current puzzle abhi chal raha hai. Answer do ya wait karo.", show_alert=True)

    difficulty = old["difficulty"] if old else "medium"
    await query.answer("🧩 Starting new puzzle...")
    await start_game(chat_id, difficulty, query.message)

# ============================================================
# RUN BOT
# ============================================================

if __name__ == "__main__":
    print("🚀 Advanced Jumble Bot is starting...")
    app.run()
