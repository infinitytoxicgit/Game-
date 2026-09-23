import asyncio
import html
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus, ParseMode
from pyrogram.errors import (
    MessageNotModified,
    RPCError,
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    UserIsBlocked,
    InputUserDeactivated,
    ChatWriteForbidden,
    FloodWait
)
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    ChatMemberUpdated
)

import rich

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
LOGGER_GROUP_ID = -1003515360437
ASSISTANT_SESSION = os.getenv("ASSISTANT_SESSION", None)

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

assistant = None
if ASSISTANT_SESSION:
    assistant = Client(
        "jumble_assistant",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=ASSISTANT_SESSION
    )

DB = sqlite3.connect("jumble_game.db", check_same_thread=False)
DB.row_factory = sqlite3.Row
LOCK = asyncio.Lock()

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
    last_daily REAL DEFAULT 0,
    exp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    point_card_exp REAL DEFAULT 0,
    level_card_exp REAL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS event_words (
    word TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS events_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    word TEXT,
    hint TEXT,
    reward_stars INTEGER,
    reward_exp INTEGER,
    interval_hrs INTEGER,
    target_type TEXT,
    next_run REAL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY,
    easy INTEGER DEFAULT 120,
    medium INTEGER DEFAULT 300,
    hard INTEGER DEFAULT 600,
    default_diff TEXT DEFAULT 'medium',
    is_active INTEGER DEFAULT 1,
    auto_delete INTEGER DEFAULT 0,
    event_active INTEGER DEFAULT 1,
    logging_enabled INTEGER DEFAULT 1
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

CREATE TABLE IF NOT EXISTS event_games (
    chat_id INTEGER PRIMARY KEY,
    event_id INTEGER,
    word TEXT,
    hint TEXT,
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
    tag TEXT DEFAULT 'normal',
    timestamp REAL
);
""")
DB.commit()

def run_migrations():
    defaults = {
        "points_easy": 10,
        "points_medium": 20,
        "points_hard": 30,
        "exp_easy": 15,
        "exp_medium": 30,
        "exp_hard": 50,
        "hints_easy": 3,
        "hints_medium": 3,
        "hints_hard": 3,
        "daily_points": 50,
        "bonus_points": 100,
        "card_point_price": 500,
        "card_point_hrs": 3,
        "card_point_req_lvl": 1,
        "card_level_price": 600,
        "card_level_hrs": 3,
        "card_level_req_lvl": 2,
        "global_exp_per_lvl": 500,
        "shop_exp_cost": 1000,
        "shop_exp_reward": 500
    }
    for k, v in defaults.items():
        DB.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)", (k, v))

    settings_cols = [c[1] for c in DB.execute("PRAGMA table_info(settings)").fetchall()]
    if "logging_enabled" not in settings_cols:
        DB.execute("ALTER TABLE settings ADD COLUMN logging_enabled INTEGER DEFAULT 1")
    if "event_active" not in settings_cols:
        DB.execute("ALTER TABLE settings ADD COLUMN event_active INTEGER DEFAULT 1")

    history_cols = [c[1] for c in DB.execute("PRAGMA table_info(score_history)").fetchall()]
    if "tag" not in history_cols:
        DB.execute("ALTER TABLE score_history ADD COLUMN tag TEXT DEFAULT 'normal'")

    for ew in rich.DEFAULT_EVENT:
        DB.execute("INSERT OR IGNORE INTO event_words (word) VALUES (?, ?)", (ew.lower().strip(),)) if False else DB.execute("INSERT OR IGNORE INTO event_words (word) VALUES (?)", (ew.lower().strip(),))

    DB.commit()

run_migrations()

WORDS = {
    "easy": list(set(w.lower() for w in rich.DEFAULT_EASY if len(w) >= 3)),
    "medium": list(set(w.lower() for w in rich.DEFAULT_MEDIUM if len(w) >= 3)),
    "hard": list(set(w.lower() for w in rich.DEFAULT_HARD if len(w) >= 3))
}

custom_rows = DB.execute("SELECT difficulty, word FROM custom_words").fetchall()
for row in custom_rows:
    diff = row["difficulty"].lower()
    w = row["word"].lower().strip()
    if diff in WORDS and w not in WORDS[diff]:
        WORDS[diff].append(w)

EVENT_WORDS = [row["word"] for row in DB.execute("SELECT word FROM event_words").fetchall()]
EVENT_WIZARD = {}

# ============================================================
# HELPER FUNCTIONS
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
    """, (user.id, user.username or "", user.first_name or "Player"))
    DB.commit()

def get_user(user_id):
    return DB.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def is_owner(user_id):
    return int(user_id) == int(OWNER_ID) if user_id else False

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
    if is_owner(user_id) or chat.type in (ChatType.PRIVATE,):
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
            INSERT INTO settings(chat_id, easy, medium, hard, default_diff, is_active, auto_delete, event_active, logging_enabled)
            VALUES (?, 120, 300, 600, 'medium', 1, 0, 1, 1)
            ON CONFLICT(chat_id) DO NOTHING
        """, (chat_id,))
        DB.commit()
        row = DB.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    return row

def is_group(message):
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

def choose_word(chat_id, difficulty):
    pool = WORDS.get(difficulty, [])[:]
    used = {row["word"] for row in DB.execute("SELECT word FROM used_words WHERE chat_id=? AND difficulty=?", (chat_id, difficulty)).fetchall()}
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

async def send_log_notification(chat_obj, user_obj, word, pts_gained, exp_gained, total_points, total_exp, level, is_event=False):
    s = get_settings(chat_obj.id)
    if not s["logging_enabled"]:
        return

    chat_title = chat_obj.title if hasattr(chat_obj, "title") and chat_obj.title else "Private DM"
    chat_username = getattr(chat_obj, "username", None)
    gc_link = f"https://t.me/{chat_username}" if chat_username else None
    if not gc_link and chat_obj.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            gc_link = await app.export_chat_invite_link(chat_obj.id)
        except Exception:
            gc_link = None

    user_link = f"https://t.me/{user_obj.username}" if user_obj.username else f"tg://openmessage?user_id={user_obj.id}"
    btn_row = [InlineKeyboardButton("👤 User Profile", url=user_link)]
    if gc_link:
        btn_row.append(InlineKeyboardButton("👥 Group Link", url=gc_link))

    tag_str = "🌟 EVENT PUZZLE SOLVED" if is_event else "🧩 JUMBLE WORD SOLVED"
    log_text = (
        f"<blockquote>📢 <b>{tag_str}</b>\n\n"
        f"👤 <b>Player:</b> {rich.get_mention(user_obj)} (<code>{user_obj.id}</code>)\n"
        f"🏷️ <b>Username:</b> @{user_obj.username if user_obj.username else 'None'}\n"
        f"👥 <b>Chat / Group:</b> <code>{html.escape(chat_title)}</code> (<code>{chat_obj.id}</code>)\n\n"
        f"✅ <b>Word:</b> <code>{word.upper()}</code>\n"
        f"⭐ <b>Stars Gained:</b> <code>+{pts_gained}</code> (Total: <code>{total_points}</code>)\n"
        f"⚡ <b>EXP Gained:</b> <code>+{exp_gained}</code> (Total: <code>{total_exp}</code> | Level <code>{level}</code>)\n"
        f"⏰ <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S')}</code></blockquote>"
    )
    try:
        await app.send_message(LOGGER_GROUP_ID, log_text, reply_markup=InlineKeyboardMarkup([btn_row]), parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Logger error: {e}")

# ============================================================
# GAME CORE
# ============================================================

async def start_game(chat_id, difficulty, message_or_chat):
    if chat_id in JUMBLE_FIGHT:
        return

    settings = get_settings(chat_id)
    if not settings["is_active"]:
        return

    old_game = DB.execute("SELECT message_id FROM games WHERE chat_id=?", (chat_id,)).fetchone()
    if old_game and settings["auto_delete"] and old_game["message_id"]:
        await rich.safe_delete_and_unpin(chat_id, old_game["message_id"])

    DB.execute("DELETE FROM games WHERE chat_id=?", (chat_id,))
    word = choose_word(chat_id, difficulty)
    jumbled = rich.jumble_word(word)
    puzzle_id = random.randint(10000, 99999)
    now = time.time()
    timer_val = settings[difficulty]
    reward_pts = get_global_config(f"points_{difficulty}", 10)
    reward_exp = get_global_config(f"exp_{difficulty}", 30)
    hint_limit = get_global_config(f"hints_{difficulty}", 3)
    expires = now + timer_val

    DB.execute("""
        INSERT INTO games(chat_id, difficulty, word, puzzle_id, started, expires, message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (chat_id, difficulty, word, puzzle_id, now, expires, 0))
    DB.commit()

    image = rich.make_puzzle_image(jumbled, difficulty, puzzle_id, exp_val=reward_exp, is_event=False)
    caption_text = (
        f"<blockquote>🧩 <b>𝐉ᴜᴍʙʟᴇ #{puzzle_id}</b>\n\n"
        f"🎯 <b>Difficulty:</b> <code>{difficulty.title()}</code>\n"
        f"⏱️ <b>Time:</b> <code>{timer_val // 60}m {timer_val % 60}s</code>\n"
        f"⭐ <b>Reward:</b> <code>+{reward_pts} Points</code>\n"
        f"⚡ <b>EXP:</b> <code>+{reward_exp} EXP</code>\n"
        f"💡 <b>Hints:</b> <code>{hint_limit}/word</code>\n\n"
        f"🔀 <i>Unscramble the letters & type in chat!</i></blockquote>"
    )

    try:
        if isinstance(message_or_chat, Message):
            sent = await message_or_chat.reply_photo(photo=image, caption=caption_text, reply_markup=rich.normal_game_keyboard(), parse_mode=ParseMode.HTML)
        else:
            sent = await app.send_photo(chat_id, photo=image, caption=caption_text, reply_markup=rich.normal_game_keyboard(), parse_mode=ParseMode.HTML)

        DB.execute("UPDATE games SET message_id=? WHERE chat_id=?", (sent.id, chat_id))
        DB.commit()
    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserIsBlocked):
        DB.execute("UPDATE settings SET is_active=0 WHERE chat_id=?", (chat_id,))
        DB.commit()
    except Exception as e:
        print(f"Error starting game in {chat_id}: {e}")

    asyncio.create_task(expire_game(chat_id, puzzle_id, expires))

async def expire_game(chat_id, puzzle_id, expires):
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
        await rich.safe_delete_and_unpin(chat_id, row["message_id"])

    try:
        exp_msg = await app.send_message(
            chat_id,
            f"<blockquote>⏰ <b>Time's Up!</b>\n\n❌ <b>Nobody solved it.</b>\n✅ <b>Answer:</b> <code>{row['word'].upper()}</code>\n\n🔄 <i>Next puzzle starting...</i></blockquote>",
            parse_mode=ParseMode.HTML
        )
        if s["auto_delete"]:
            asyncio.create_task(rich.delete_after(exp_msg, 4))
    except Exception:
        pass

    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if chat_id not in JUMBLE_FIGHT and s["is_active"]:
        asyncio.create_task(start_game(chat_id, s["default_diff"], chat_id))

# ============================================================
# EVENT DISPATCHER & SCHEDULER
# ============================================================

async def dispatch_event_by_id(event_id, target_chat_id=None):
    ev = DB.execute("SELECT * FROM events_bank WHERE id=?", (event_id,)).fetchone()
    if not ev or not ev["is_active"]:
        return

    now = time.time()
    expires = now + 180

    targets = []
    if target_chat_id:
        targets = [target_chat_id]
    elif ev["target_type"] == "dm":
        users = DB.execute("SELECT user_id FROM users").fetchall()
        targets = [u["user_id"] for u in users]
    elif ev["target_type"] == "group":
        s_rows = DB.execute("SELECT chat_id FROM settings WHERE is_active=1 AND event_active=1 AND chat_id != 0").fetchall()
        targets = [r["chat_id"] for r in s_rows]
    else:
        users = DB.execute("SELECT user_id FROM users").fetchall()
        s_rows = DB.execute("SELECT chat_id FROM settings WHERE is_active=1 AND event_active=1 AND chat_id != 0").fetchall()
        targets = [u["user_id"] for u in users] + [r["chat_id"] for r in s_rows]

    for tid in targets:
        chosen_w = random.choice(EVENT_WORDS) if (ev["word"].lower() == "random" and EVENT_WORDS) else ev["word"]
        jumbled = rich.jumble_word(chosen_w)
        puzzle_id = random.randint(10000, 99999)
        image = rich.make_puzzle_image(jumbled, ev["title"], puzzle_id, exp_val=ev["reward_exp"], is_event=True)
        hint_text = f"\n💡 <b>Hint:</b> <code>{ev['hint']}</code>" if ev["hint"] and ev["hint"] != "0" else ""

        caption_text = (
            f"<blockquote>🌟 <b>{ev['title'].upper()} EVENT DROP!</b>\n\n"
            f"💎 <b>Reward Stars:</b> <code>+{ev['reward_stars']} pts</code>\n"
            f"⚡ <b>Reward EXP:</b> <code>+{ev['reward_exp']} EXP</code>{hint_text}\n"
            f"⏱️ <b>Time Limit:</b> <code>3 minutes</code></blockquote>\n\n"
            f"<blockquote>⚡ <i>Unscramble first to claim victory!</i></blockquote>"
        )

        try:
            old_ev = DB.execute("SELECT message_id FROM event_games WHERE chat_id=?", (tid,)).fetchone()
            if old_ev and old_ev["message_id"]:
                await rich.safe_delete_and_unpin(tid, old_ev["message_id"])
            DB.execute("DELETE FROM event_games WHERE chat_id=?", (tid,))
            DB.commit()

            sent = await app.send_photo(tid, photo=image, caption=caption_text, parse_mode=ParseMode.HTML)
            DB.execute("""
                INSERT INTO event_games(chat_id, event_id, word, hint, puzzle_id, started, expires, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (tid, ev["id"], chosen_w, ev["hint"], puzzle_id, now, expires, sent.id))
            DB.commit()
            asyncio.create_task(expire_event_game(tid, puzzle_id, expires))
        except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserIsBlocked):
            DB.execute("UPDATE settings SET is_active=0, event_active=0 WHERE chat_id=?", (tid,))
            DB.commit()
        except Exception as e:
            print(f"Error dispatching event to {tid}: {e}")

    DB.execute("UPDATE events_bank SET next_run=? WHERE id=?", (now + (ev["interval_hrs"] * 3600), event_id))
    DB.commit()

async def event_scheduler_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        due_events = DB.execute("SELECT id FROM events_bank WHERE is_active=1 AND next_run <= ?", (now,)).fetchall()
        for de in due_events:
            try:
                await dispatch_event_by_id(de["id"])
            except Exception as e:
                print(f"Event scheduler error: {e}")

async def expire_event_game(chat_id, puzzle_id, expires):
    await asyncio.sleep(max(0, expires - time.time()))
    row = DB.execute("SELECT * FROM event_games WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id)).fetchone()
    if not row or row["solved"]:
        return

    DB.execute("UPDATE event_games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    if row["message_id"]:
        await rich.safe_delete_and_unpin(chat_id, row["message_id"])

    try:
        t_msg = await app.send_message(
            chat_id,
            f"<blockquote>⏰ <b>Event Word Expired!</b>\n\n❌ Kisi ne solve nahi kiya.\n✅ <b>Word:</b> <code>{row['word'].upper()}</code></blockquote>",
            parse_mode=ParseMode.HTML
        )
        asyncio.create_task(rich.delete_after(t_msg, 5))
    except Exception:
        pass

# ============================================================
# JUMBLE FIGHT (1v1) & BET FIGHT ENGINE
# ============================================================

JUMBLE_FIGHT = {}
FIGHT_LOBBY = {}
REBET_LOBBY = {}

async def fight_timeout_task(chat_id, round_num, timer_duration):
    await asyncio.sleep(timer_duration)
    should_advance = False
    async with LOCK:
        game = JUMBLE_FIGHT.get(chat_id)
        if game and game["round"] == round_num:
            word = game["word"]
            s = get_settings(chat_id)
            if s["auto_delete"] and game.get("msg_id"):
                await rich.safe_delete_and_unpin(chat_id, game["msg_id"])

            try:
                t_msg = await app.send_message(
                    chat_id,
                    f"<blockquote>⏰ <b>𝐑ᴏᴜɴᴅ {round_num} 𝐓ɪᴍᴇᴏᴜᴛ!</b>\n❌ <b>Nobody solved.</b>\n✅ <b>Answer:</b> <code>{word.upper()}</code>\n\n🔄 <i>Next round starting...</i></blockquote>",
                    parse_mode=ParseMode.HTML
                )
                if s["auto_delete"]:
                    asyncio.create_task(rich.delete_after(t_msg, 4))
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
    jumbled = rich.jumble_word(word)

    game["word"] = word
    game["expires"] = time.time() + game["timer"]
    per_round_hints = int(get_global_config(f"hints_{diff}", 3))
    game["max_hints"] = per_round_hints
    game["hints_left"] = {p: per_round_hints for p in game["players"]}
    game["round_revealed"] = {p: [] for p in game["players"]}

    fight_tag = "BET FIGHT" if game.get("is_bet") else "FIGHT"
    image = rich.make_puzzle_image(jumbled, f"{fight_tag} {diff.upper()}", game["round"])
    title_header = "💰 <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐁𝐄𝐓 𝐅𝐈𝐆𝐇𝐓" if game.get("is_bet") else "⚔️ <b>𝐉𝐔𝐌𝐁𝐋𝐄 𝐅𝐈𝐆𝐇𝐓"
    extra_info = f"\n💵 <b>Bet:</b> <code>{game.get('bet_amount')} pts</code>" if game.get("is_bet") else ""
    p1, p2 = game["players"]

    try:
        sent = await app.send_photo(
            chat_id,
            photo=image,
            caption=(
                f"<blockquote>{title_header} — ROUND {game['round']}/10</b>\n\n"
                f"🎯 <b>Difficulty:</b> <code>{diff.title()}</code>\n"
                f"⏱️ <b>Time:</b> <code>{game['timer']}s</code>{extra_info}\n"
                f"💡 <b>Hints:</b> <code>{per_round_hints} each</code>\n"
                f"👥 <b>Players:</b> {game['mentions'][p1]} 🆚 {game['mentions'][p2]}</blockquote>"
            ),
            reply_markup=rich.fight_match_keyboard(),
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
        await rich.safe_delete_and_unpin(chat_id, game["msg_id"])

    p1, p2 = game["players"]
    s1, s2 = game["scores"][p1], game["scores"][p2]
    is_bet = game.get("is_bet", False)
    bet_amt = game.get("bet_amount", 0)
    is_rebet = game.get("is_rebet", False)
    now = time.time()

    winner = p1 if s1 > s2 else (p2 if s2 > s1 else None)
    loser = p2 if winner == p1 else (p1 if winner == p2 else None)
    w_score = max(s1, s2)
    l_score = min(s1, s2)
    m1, m2 = game["mentions"][p1], game["mentions"][p2]
    end_kb = None

    if not is_bet:
        if winner:
            DB.execute("UPDATE users SET fight_wins=fight_wins+1 WHERE user_id=?", (winner,))
            DB.execute("UPDATE users SET fight_losses=fight_losses+1 WHERE user_id=?", (loser,))
            DB.commit()
            result = f"<blockquote>🏁 <b>JUMBLE FIGHT OVER!</b>\n\n👤 {m1} — <b>{s1} pts</b>\n👤 {m2} — <b>{s2} pts</b>\n\n🏆 <b>Winner:</b> {game['mentions'][winner]} 🎉</blockquote>"
        else:
            result = f"<blockquote>🏁 <b>JUMBLE FIGHT OVER!</b>\n\n👤 {m1} — <b>{s1} pts</b>\n👤 {m2} — <b>{s2} pts</b>\n\n🤝 <b>Match Draw!</b></blockquote>"
    else:
        if winner:
            if is_rebet:
                total_payout = (bet_amt * 2) + 100
                DB.execute("UPDATE users SET points=points+?, bet_wins=bet_wins+1 WHERE user_id=?", (total_payout, winner))
                DB.execute("UPDATE users SET bet_losses=bet_losses+1 WHERE user_id=?", (loser,))
                DB.execute("INSERT INTO score_history (user_id, chat_id, points, tag, timestamp) VALUES (?, ?, ?, 'bet_fight', ?)", (winner, chat_id, total_payout, now))
                DB.commit()
                result = (
                    f"<blockquote>💰 <b>COMEBACK RE-BET FIGHT OVER!</b>\n\n"
                    f"👤 {game['mentions'][winner]} — <b>{w_score} pts</b> (WINNER)\n"
                    f"👤 {game['mentions'][loser]} — <b>{l_score} pts</b>\n\n"
                    f"🏆 <b>Total Payout:</b> <code>+{total_payout} points</code> to {game['mentions'][winner]}!</blockquote>"
                )
            else:
                total_pot = bet_amt * 2
                win_reward = int(total_pot * 0.75)
                loser_cashback = total_pot - win_reward
                rebet_stake = int(bet_amt * 0.25)

                DB.execute("UPDATE users SET points=points+?, bet_wins=bet_wins+1 WHERE user_id=?", (win_reward, winner))
                DB.execute("UPDATE users SET points=points+?, bet_losses=bet_losses+1 WHERE user_id=?", (loser_cashback, loser))
                DB.execute("INSERT INTO score_history (user_id, chat_id, points, tag, timestamp) VALUES (?, ?, ?, 'bet_fight', ?)", (winner, chat_id, win_reward, now))
                DB.execute("INSERT INTO score_history (user_id, chat_id, points, tag, timestamp) VALUES (?, ?, ?, 'bet_fight', ?)", (loser, chat_id, loser_cashback, now))
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

                end_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🔁 25% Re-Bet ({rebet_stake} pts) + 100 Bonus", callback_data="rebet_challenge")]])
                result = (
                    f"<blockquote>💰 <b>JUMBLE BET FIGHT OVER!</b>\n\n"
                    f"👤 {game['mentions'][winner]} — <b>{w_score} pts</b> (WINNER)\n"
                    f"👤 {game['mentions'][loser]} — <b>{l_score} pts</b>\n\n"
                    f"🏆 <b>75% Winner Reward:</b> <code>+{win_reward} pts</code>\n"
                    f"🛡️ <b>25% Loser Cashback:</b> <code>+{loser_cashback} pts</code></blockquote>"
                )
        else:
            DB.execute("UPDATE users SET points=points+? WHERE user_id=?", (bet_amt, p1))
            DB.execute("UPDATE users SET points=points+? WHERE user_id=?", (bet_amt, p2))
            DB.execute("INSERT INTO score_history (user_id, chat_id, points, tag, timestamp) VALUES (?, ?, ?, 'bet_fight', ?)", (p1, chat_id, bet_amt, now))
            DB.execute("INSERT INTO score_history (user_id, chat_id, points, tag, timestamp) VALUES (?, ?, ?, 'bet_fight', ?)", (p2, chat_id, bet_amt, now))
            DB.commit()
            result = f"<blockquote>🤝 <b>JUMBLE BET FIGHT DRAW!</b>\n\nRefunded stake of <code>{bet_amt} points</code> to both players.</blockquote>"

    await app.send_message(chat_id, result, reply_markup=end_kb, parse_mode=ParseMode.HTML)
    await asyncio.sleep(3)
    s = get_settings(chat_id)
    if s["is_active"]:
        await app.send_message(chat_id, "<blockquote>🔄 <i>Resuming normal Jumble Game...</i></blockquote>", parse_mode=ParseMode.HTML)
        asyncio.create_task(start_game(chat_id, s["default_diff"], chat_id))

# ============================================================
# BROADCAST ENGINE
# ============================================================

@app.on_message(filters.command("broadcast"))
async def broadcast_cmd_handler(client: Client, message: Message):
    if not message.from_user or not is_authed(message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Sirf Authorized users broadcast chala sakte hain.</b></blockquote>", parse_mode=ParseMode.HTML)

    raw_text = message.text or ""
    parts = raw_text.split()
    cmd_args = parts[1:]

    pin = "-pin" in cmd_args
    pinloud = "-pinloud" in cmd_args
    send_users = "-user" in cmd_args
    use_assistant = "-assistant" in cmd_args
    no_bot = "-nobot" in cmd_args

    flags = {"-pin", "-pinloud", "-user", "-assistant", "-nobot"}
    content_parts = [p for p in cmd_args if p.lower() not in flags]
    broadcast_text = " ".join(content_parts)

    reply_msg = message.reply_to_message
    if not broadcast_text and not reply_msg:
        return await message.reply_text(
            "<blockquote>✦ <b>ʙʀσᴧᴅᴄᴧsᴛ ғєᴧᴛυʀє :</b>\n"
            "✦ (Auths σηʟʏ)\n\n"
            "✦ <code>/broadcast [ϻєssᴧɢє / ʀєᴘʟʏ]</code>\n"
            "➥ sєηᴅ ϻєssᴧɢє ᴛσ ᴧʟʟ sєʀᴠєᴅ ᴄʜᴧᴛs.\n\n"
            "✦ <b>ʙʀσᴧᴅᴄᴧsᴛ ϻσᴅєs :</b>\n"
            "• <code>-pin</code> — ᴘɪη ϻєssᴧɢє ɪη ᴄʜᴧᴛs.\n"
            "• <code>-pinloud</code> — ᴘɪη + ησᴛɪғɪᴄᴧᴛɪση.\n"
            "• <code>-user</code> — sєηᴅ ᴛσ υsєʀs (DMs).\n"
            "• <code>-assistant</code> — sєηᴅ ғʀσϻ ᴧssɪsᴛᴧηᴛ.\n"
            "• <code>-nobot</code> — ʙσᴛ ᴡση’ᴛ sєηᴅ.\n\n"
            "✦ <b>єxᴧϻᴘʟє :</b>\n"
            "<code>/broadcast -user -pin ᴛєsᴛ ϻєssᴧɢє</code></blockquote>",
            parse_mode=ParseMode.HTML
        )

    if use_assistant and not assistant:
        return await message.reply_text("<blockquote>❌ <b>Assistant client configured nahi hai (ASSISTANT_SESSION missing).</b></blockquote>", parse_mode=ParseMode.HTML)

    if no_bot and not use_assistant:
        return await message.reply_text("<blockquote>❌ <b>-nobot ke sath -assistant flag zaroori hai.</b></blockquote>", parse_mode=ParseMode.HTML)

    if send_users:
        user_rows = DB.execute("SELECT user_id FROM users").fetchall()
        targets = [u["user_id"] for u in user_rows]
    else:
        chat_rows = DB.execute("SELECT chat_id FROM settings WHERE chat_id != 0").fetchall()
        targets = [c["chat_id"] for c in chat_rows]

    total_targets = len(targets)
    if total_targets == 0:
        return await message.reply_text("<blockquote>⚠️ <b>Target list empty hai. Koi chats/users nahi mile.</b></blockquote>", parse_mode=ParseMode.HTML)

    active_flags = [f for f in ["-pin" if pin else "", "-pinloud" if pinloud else "", "-user" if send_users else "", "-assistant" if use_assistant else "", "-nobot" if no_bot else ""] if f]
    status_msg = await message.reply_text(rich.build_broadcast_status_text(total_targets, 0, 0, active_flags, is_complete=False), parse_mode=ParseMode.HTML)

    success = 0
    failed = 0

    sender_client = assistant if (use_assistant and no_bot) else client

    for idx, tid in enumerate(targets, 1):
        try:
            sent = None
            if not no_bot:
                if reply_msg:
                    sent = await reply_msg.copy(tid)
                else:
                    sent = await client.send_message(tid, broadcast_text)
            elif use_assistant and assistant:
                if reply_msg:
                    sent = await reply_msg.copy(tid)
                else:
                    sent = await assistant.send_message(tid, broadcast_text)

            if (pin or pinloud) and sent:
                try:
                    await sent.pin(disable_notification=not pinloud)
                except Exception:
                    pass

            success += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                if reply_msg:
                    sent = await reply_msg.copy(tid)
                else:
                    sent = await sender_client.send_message(tid, broadcast_text)
                success += 1
            except Exception:
                failed += 1
        except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserIsBlocked, InputUserDeactivated, ChatWriteForbidden):
            failed += 1
        except Exception:
            failed += 1

        if idx % 20 == 0 or idx == total_targets:
            try:
                await status_msg.edit_text(rich.build_broadcast_status_text(total_targets, success, failed, active_flags, is_complete=(idx == total_targets)), parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            await asyncio.sleep(0.5)

# ============================================================
# COMMAND DISPATCHER & WIZARD ENGINE
# ============================================================

ALL_BOT_COMMANDS = {
    "start", "help", "jumble", "shop", "store", "setevent", "cancel", "setglobalexp",
    "storeexpprize", "setexp", "eventlist", "events", "startevent", "stopevent",
    "addeventword", "deleventword", "eventwords", "setcard", "stats", "calculate", "log",
    "broadcast", "addstar", "addpoints", "deductstar", "deductpoints", "removestar", "auth",
    "unauth", "authlist", "update", "gitpull", "settings", "setting", "setpoints", "sethint",
    "setdaily", "setbonus", "daily", "bonus", "private", "public", "jumblefight", "fight",
    "rapido", "jumblebetfight", "betfight", "leaderboard", "top", "rank", "lb", "backup",
    "dbbackup", "getdb", "addword", "addwords", "delword", "delallword", "delallwords"
}

@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    ensure_user(message.from_user)
    text = (
        "<blockquote>🧩 <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐓ᴏ 𝐀ᴅᴠᴀɴᴄᴇᴅ 𝐉ᴜᴍʙʟᴇ 𝐁ᴏᴛ!</b></blockquote>\n\n"
        "<blockquote>🎮 <b>𝐆ᴀᴍᴇ 𝐂ᴏᴍᴍᴀɴᴅs:</b>\n"
        "• <code>/jumble</code> — Start Auto-loop Jumble Game\n"
        "• <code>/jumblefight @user</code> — 1v1 Battle Mode\n"
        "• <code>/jumblebetfight [mode] [amount] @user</code> — 1v1 Bet Battle\n"
        "• <code>/shop</code> — Power & EXP Shop\n"
        "• <code>/eventlist</code> — Active Mystery Events\n"
        "• <code>/settings</code> — Admin Group Panel\n"
        "• <code>/broadcast</code> — Auth Broadcasting Suite</blockquote>"
    )
    dm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 𝐒ᴜᴘᴘᴏʀᴛ", url=SUPPORT_GC), InlineKeyboardButton("➕ 𝐀ᴅᴅ 𝐌ᴇ", url=ADD_ME_URL)],
        [InlineKeyboardButton("🛍️ 𝐎ᴘᴇɴ 𝐒ʜᴏᴘ", callback_data="open_shop_btn"), InlineKeyboardButton("📅 𝐄ᴠᴇɴᴛ 𝐋ɪsᴛ", callback_data="open_eventlist_btn")]
    ])
    if message.chat.type in (ChatType.PRIVATE,):
        try:
            await message.reply_photo(photo=START_IMG, caption=text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
        except Exception:
            await message.reply_text(text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, reply_markup=dm_markup, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("jumble"))
async def jumble_cmd_handler(_, message: Message):
    DB.execute("UPDATE settings SET is_active=1 WHERE chat_id=?", (message.chat.id,))
    DB.commit()
    s = get_settings(message.chat.id)
    diff = s["default_diff"]
    if len(message.command) > 1 and message.command[1].lower() in WORDS:
        diff = message.command[1].lower()
    await start_game(message.chat.id, diff, message)

@app.on_message(filters.command("setevent"))
async def set_event_cmd_handler(_, message: Message):
    if not is_authed(message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Authorized users only.</b></blockquote>", parse_mode=ParseMode.HTML)
    EVENT_WIZARD[message.from_user.id] = {"step": 1, "title": "", "word": "", "hint": "0", "stars": 0, "exp": 0, "interval": 4, "target": "group"}
    await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 1/7)</b>\n\nEvent ka Title/Naam likhein:</blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("cancel"))
async def cancel_wizard_handler(_, message: Message):
    if message.from_user and message.from_user.id in EVENT_WIZARD:
        del EVENT_WIZARD[message.from_user.id]
        await message.reply_text("<blockquote>❌ <b>Event wizard cancelled.</b></blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("setglobalexp"))
async def set_global_exp_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return
    nums = re.findall(r"\d+", message.text)
    if not nums:
        return await message.reply_text("<blockquote>Usage: <code>/setglobalexp 500</code></blockquote>", parse_mode=ParseMode.HTML)
    val = int(nums[0])
    set_global_config("global_exp_per_lvl", val)
    await message.reply_text(f"<blockquote>✅ <b>Global Level EXP Requirement set to {val} EXP!</b></blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("storeexpprize"))
async def set_store_exp_prize_cmd(_, message: Message):
    if not is_authed(message.from_user.id):
        return
    nums = [int(n) for n in re.findall(r"\d+", message.text)]
    if len(nums) < 2:
        return await message.reply_text("<blockquote>Usage: <code>/storeexpprize [cost] [exp]</code></blockquote>", parse_mode=ParseMode.HTML)
    set_global_config("shop_exp_cost", nums[0])
    set_global_config("shop_exp_reward", nums[1])
    await message.reply_text(f"<blockquote>✅ <b>Shop EXP Pack set: {nums[0]} stars for {nums[1]} EXP!</b></blockquote>", parse_mode=ParseMode.HTML)

@app.on_message(filters.command("stats"))
async def stats_cmd_handler(_, message: Message):
    target_user = message.from_user
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
    elif len(message.command) >= 2:
        arg = message.command[1]
        try:
            target_user = await app.get_users(int(arg) if arg.isdigit() else arg)
        except Exception:
            pass

    ensure_user(target_user)
    u = get_user(target_user.id)
    step = get_global_config("global_exp_per_lvl", 500)
    now = time.time()
    pt_status = rich.format_duration(u["point_card_exp"] - now) if u and u["point_card_exp"] > now else "None"
    lvl_status = rich.format_duration(u["level_card_exp"] - now) if u and u["level_card_exp"] > now else "None"

    await message.reply_text(
        f"<blockquote>👤 <b>Player:</b> {rich.get_mention(target_user)}\n"
        f"🎖️ <b>Level:</b> <code>{u['level']}</code> (<code>{u['exp'] % step}/{step} EXP</code>)\n"
        f"⭐ <b>Stars:</b> <code>{u['points']}</code> | 🧩 <b>Solved:</b> <code>{u['solved']}</code>\n"
        f"⚡ <b>2x Point Power:</b> <code>{pt_status}</code>\n"
        f"⚡ <b>2x Level Power:</b> <code>{lvl_status}</code></blockquote>",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.command(["shop", "store"]))
async def shop_cmd_handler(_, message: Message):
    text, kb = rich.build_shop_text_and_kb(message.from_user.id)
    await message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@app.on_message(filters.command(["eventlist", "events"]))
async def eventlist_cmd_handler(_, message: Message):
    events = DB.execute("SELECT * FROM events_bank").fetchall()
    if not events:
        return await message.reply_text("<blockquote>📅 <b>No active events in bank.</b></blockquote>", parse_mode=ParseMode.HTML)
    text = "<blockquote>📅 <b>MYSTERY EVENTS LIST</b>\n\n"
    btns = []
    for ev in events:
        text += f"🔹 <b>{ev['title']}</b> (ID: <code>{ev['id']}</code>) | Target: <code>{ev['target_type']}</code>\n"
        btns.append([InlineKeyboardButton(f"🛑 Toggle #{ev['id']}", callback_data=f"ev_toggle_{ev['id']}"), InlineKeyboardButton(f"🗑️ Delete #{ev['id']}", callback_data=f"ev_del_{ev['id']}")])
    text += "</blockquote>"
    btns.append([InlineKeyboardButton("❌ Close", callback_data="close_panel")])
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.HTML)

@app.on_message(filters.command(["settings", "setting"]))
async def settings_cmd_handler(_, message: Message):
    if not message.from_user or not await is_admin_or_owner(message.chat, message.from_user.id):
        return await message.reply_text("<blockquote>❌ <b>Only group admins can configure settings.</b></blockquote>", parse_mode=ParseMode.HTML)
    s = get_settings(message.chat.id)
    text = (
        f"<blockquote>⚙️ <b>𝐉ᴜᴍʙʟᴇ 𝐆ʀᴏᴜᴘ 𝐒ᴇᴛᴛɪɴɢs</b>\n\n"
        f"🟢 <b>Status:</b> <code>{'Running' if s['is_active'] else 'Stopped'}</code>\n"
        f"🗑️ <b>Auto Delete Old:</b> <code>{'Enabled' if s['auto_delete'] else 'Disabled'}</code>\n"
        f"🎯 <b>Mode:</b> <code>{s['default_diff'].title()}</code>\n"
        f"⏱️ <b>Timers:</b> Easy: <code>{s['easy']}s</code> | Med: <code>{s['medium']}s</code> | Hard: <code>{s['hard']}s</code></blockquote>"
    )
    await message.reply_text(text, reply_markup=rich.build_settings_keyboard(s), parse_mode=ParseMode.HTML)

# ============================================================
# UNIVERSAL TEXT DISPATCHER & CALLBACK ROUTER
# ============================================================

@app.on_message(filters.text)
async def universal_text_dispatcher(_, message: Message):
    if not message.from_user or not message.text:
        return
    uid = message.from_user.id
    txt = message.text.strip()

    if uid in EVENT_WIZARD and not txt.startswith("/"):
        w = EVENT_WIZARD[uid]
        step = w["step"]
        if step == 1:
            w["title"] = txt
            w["step"] = 2
            return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 2/7)</b>\n\nUnique Word likhein (ya <code>random</code>):</blockquote>", parse_mode=ParseMode.HTML)
        elif step == 2:
            w["word"] = txt.lower().strip()
            w["step"] = 3
            return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 3/7)</b>\n\nHint likhein (ya <code>0</code>):</blockquote>", parse_mode=ParseMode.HTML)
        elif step == 3:
            w["hint"] = txt
            w["step"] = 4
            return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 4/7)</b>\n\nReward Stars kitne honge?:</blockquote>", parse_mode=ParseMode.HTML)
        elif step == 4:
            nums = re.findall(r"\d+", txt)
            if nums:
                w["stars"] = int(nums[0])
                w["step"] = 5
                return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 5/7)</b>\n\nReward EXP kitna hoga?:</blockquote>", parse_mode=ParseMode.HTML)
        elif step == 5:
            nums = re.findall(r"\d+", txt)
            if nums:
                w["exp"] = int(nums[0])
                w["step"] = 6
                return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 6/7)</b>\n\nRepeat Interval (Ghante) likhein:</blockquote>", parse_mode=ParseMode.HTML)
        elif step == 6:
            nums = re.findall(r"\d+", txt)
            if nums:
                w["interval"] = int(nums[0])
                w["step"] = 7
                return await message.reply_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 7/7)</b>\n\nTarget choose karein:</blockquote>", reply_markup=rich.build_wizard_target_keyboard(), parse_mode=ParseMode.HTML)

    if txt.startswith("/") or txt.startswith("!") or txt.startswith("."):
        return

    chat_id = message.chat.id
    cleaned = "".join(c.lower() for c in txt if c.isalnum())
    now = time.time()

    # Event Mystery Check
    ev_game = DB.execute("SELECT * FROM event_games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if ev_game and now <= ev_game["expires"] and cleaned == "".join(c.lower() for c in ev_game["word"] if c.isalnum()):
        DB.execute("UPDATE event_games SET solved=1 WHERE chat_id=?", (chat_id,))
        ensure_user(message.from_user)
        u = get_user(uid)
        ev = DB.execute("SELECT * FROM events_bank WHERE id=?", (ev_game["event_id"],)).fetchone()
        b_pts = ev["reward_stars"] if ev else 250
        b_exp = ev["reward_exp"] if ev else 500

        new_exp = (u["exp"] or 0) + b_exp
        new_lvl = (new_exp // get_global_config("global_exp_per_lvl", 500)) + 1
        DB.execute("UPDATE users SET points=points+?, exp=?, level=? WHERE user_id=?", (b_pts, new_exp, new_lvl, uid))
        DB.commit()

        if ev_game["message_id"]:
            await rich.safe_delete_and_unpin(chat_id, ev_game["message_id"])

        await message.reply_text(f"<blockquote>🎉 <b>EVENT PUZZLE SOLVED!</b>\n\n👤 {rich.get_mention(message.from_user)}\n✅ <b>Word:</b> <code>{ev_game['word'].upper()}</code>\n⭐ <b>+{b_pts} stars</b> | ⚡ <b>+{b_exp} EXP</b></blockquote>", parse_mode=ParseMode.HTML)
        asyncio.create_task(send_log_notification(message.chat, message.from_user, ev_game["word"], b_pts, b_exp, u['points'] + b_pts, new_exp, new_lvl, is_event=True))
        return

    # Normal Loop Puzzle Check
    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if game and now <= game["expires"] and cleaned == "".join(c.lower() for c in game["word"] if c.isalnum()):
        DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
        ensure_user(message.from_user)
        u = get_user(uid)
        pts = get_global_config(f"points_{game['difficulty']}", 10)
        exp_gain = get_global_config(f"exp_{game['difficulty']}", 30)

        new_exp = (u["exp"] or 0) + exp_gain
        new_lvl = (new_exp // get_global_config("global_exp_per_lvl", 500)) + 1
        DB.execute("UPDATE users SET points=points+?, solved=solved+1, exp=?, level=? WHERE user_id=?", (pts, new_exp, new_lvl, uid))
        DB.commit()

        if game["message_id"]:
            await rich.safe_delete_and_unpin(chat_id, game["message_id"])

        await message.reply_text(
            f"<blockquote>🎉 <b>CORRECT!</b>\n\n👤 {rich.get_mention(message.from_user)}\n✅ <b>Answer:</b> <code>{game['word'].upper()}</code>\n⭐ <b>+{pts} points</b> | ⚡ <b>+{exp_gain} EXP</b> (Lvl {new_lvl})</blockquote>",
            parse_mode=ParseMode.HTML
        )
        asyncio.create_task(send_log_notification(message.chat, message.from_user, game["word"], pts, exp_gain, u['points'] + pts, new_exp, new_lvl, is_event=False))

        s = get_settings(chat_id)
        if s["is_active"]:
            await asyncio.sleep(3)
            await start_game(chat_id, s["default_diff"], chat_id)

@app.on_callback_query()
async def callback_router(_, query: CallbackQuery):
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    now = time.time()

    if data.startswith("ev_target_"):
        target_mode = data.split("_")[2]
        if user_id in EVENT_WIZARD:
            EVENT_WIZARD[user_id]["target"] = target_mode
            w = EVENT_WIZARD[user_id]
            hint_str = w['hint'] if w['hint'] != "0" else "None"
            review_text = (
                f"<blockquote>📋 <b>EVENT CONFIRMATION REVIEW</b>\n\n"
                f"📌 <b>Title:</b> <code>{w['title']}</code>\n"
                f"🔑 <b>Word:</b> <code>{w['word'].upper()}</code>\n"
                f"💡 <b>Hint:</b> <code>{hint_str}</code>\n"
                f"⭐ <b>Stars:</b> <code>{w['stars']} pts</code> | ⚡ <b>EXP:</b> <code>{w['exp']} EXP</code>\n"
                f"⏰ <b>Repeat:</b> Every <code>{w['interval']} Hours</code>\n"
                f"🎯 <b>Target:</b> <code>{w['target'].upper()}</code></blockquote>"
            )
            return await query.message.edit_text(review_text, reply_markup=rich.build_wizard_review_keyboard(), parse_mode=ParseMode.HTML)

    elif data == "ev_confirm_save":
        if user_id in EVENT_WIZARD:
            w = EVENT_WIZARD[user_id]
            DB.execute("""
                INSERT INTO events_bank (title, word, hint, reward_stars, reward_exp, interval_hrs, target_type, next_run, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (w["title"], w["word"], w["hint"], w["stars"], w["exp"], w["interval"], w["target"], now + (w["interval"] * 3600)))
            DB.commit()
            del EVENT_WIZARD[user_id]
            return await query.message.edit_text("<blockquote>✅ <b>Event Successfully Created & Scheduled! Check with /eventlist.</b></blockquote>", parse_mode=ParseMode.HTML)

    elif data == "ev_restart_wizard":
        EVENT_WIZARD[user_id] = {"step": 1, "title": "", "word": "", "hint": "0", "stars": 0, "exp": 0, "interval": 4, "target": "group"}
        return await query.message.edit_text("<blockquote>🌟 <b>EVENT CREATOR WIZARD (Step 1/7)</b>\n\nEvent ka Title/Naam likhein:</blockquote>", parse_mode=ParseMode.HTML)

    elif data == "ev_cancel_wizard":
        if user_id in EVENT_WIZARD:
            del EVENT_WIZARD[user_id]
        return await query.message.edit_text("<blockquote>❌ <b>Event wizard cancelled.</b></blockquote>", parse_mode=ParseMode.HTML)

    elif data.startswith("ev_del_"):
        if is_authed(user_id):
            ev_id = int(data.split("_")[2])
            DB.execute("DELETE FROM events_bank WHERE id=?", (ev_id,))
            DB.commit()
            return await query.message.edit_text(f"<blockquote>🗑️ <b>Event #{ev_id} deleted.</b></blockquote>", parse_mode=ParseMode.HTML)

    elif data.startswith("ev_toggle_"):
        if is_authed(user_id):
            ev_id = int(data.split("_")[2])
            ev = DB.execute("SELECT is_active FROM events_bank WHERE id=?", (ev_id,)).fetchone()
            if ev:
                new_st = 0 if ev["is_active"] else 1
                DB.execute("UPDATE events_bank SET is_active=? WHERE id=?", (new_st, ev_id))
                DB.commit()
                return await query.message.edit_text(f"<blockquote>⚙️ <b>Event #{ev_id} changed to: {'Activated' if new_st else 'Paused'}</b></blockquote>", parse_mode=ParseMode.HTML)

    elif data == "open_shop_btn" or data == "refresh_shop":
        ensure_user(query.from_user)
        text, kb = rich.build_shop_text_and_kb(user_id)
        return await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    elif data == "buy_instant_exp":
        ensure_user(query.from_user)
        u = get_user(user_id)
        cost = get_global_config("shop_exp_cost", 1000)
        exp_rew = get_global_config("shop_exp_reward", 500)
        if (u["points"] or 0) < cost:
            return await query.message.reply_text(f"<blockquote>❌ <b>Stars kam hain! Price: {cost} stars.</b></blockquote>", parse_mode=ParseMode.HTML)
        new_exp = (u["exp"] or 0) + exp_rew
        new_lvl = (new_exp // get_global_config("global_exp_per_lvl", 500)) + 1
        DB.execute("UPDATE users SET points = points - ?, exp = ?, level = ? WHERE user_id = ?", (cost, new_exp, new_lvl, user_id))
        DB.commit()
        text, kb = rich.build_shop_text_and_kb(user_id)
        return await query.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    elif data == "close_panel":
        return await query.message.delete()

# ============================================================
# MAIN APPLICATION STARTUP
# ============================================================

async def main():
    await app.start()
    if assistant:
        try:
            await assistant.start()
        except Exception as e:
            print(f"Assistant start warning: {e}")

    print("🚀 Advanced Jumble, Bet Fight, Level, Shop & Event Bot Started Successfully!")
    asyncio.create_task(event_scheduler_loop())
    asyncio.create_task(auto_backup_task())

    rows = DB.execute("SELECT chat_id, default_diff FROM settings WHERE is_active = 1 AND chat_id != 0").fetchall()
    for row in rows:
        try:
            await start_game(row["chat_id"], row["default_diff"] or "medium", row["chat_id"])
            await asyncio.sleep(0.5)
        except Exception:
            pass

if __name__ == "__main__":
    app.run(main())
