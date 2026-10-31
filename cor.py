import asyncio
import os
import random
import time
from database import DB, get_settings, get_global_config, ensure_user
from helpers import (
    safe_delete_and_unpin, 
    safe_pin_and_clean,
    delete_after, 
    is_admin_or_owner, 
    is_owner, 
    is_authed, 
    ACTIVE_FIGHTS
)
from image_gen import make_puzzle_image
from pyrogram import Client, filters, enums, types
from pyrogram.types import CallbackQuery, Message
from pyrogram.errors import (
    ChannelInvalid, 
    ChannelPrivate, 
    ChatAdminRequired, 
    UserIsBlocked, 
    PeerIdInvalid,
    InputUserDeactivated
)
from word_bank import choose_word, jumble_word
from utils.rich import html_to_rich_blocks, send_jumble_rich


async def check_admin_safe(chat, user_id: int) -> bool:
    if is_owner(user_id) or is_authed(user_id):
        return True
    try:
        res = await is_admin_or_owner(chat, user_id)
        if res is not None:
            return bool(res)
    except TypeError:
        try:
            return bool(await is_admin_or_owner(chat.id, user_id))
        except Exception:
            pass
    except Exception:
        pass

    try:
        member = await chat.get_member(user_id)
        return member.status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


def rich_game_buttons(puzzle_id: int):
    return [
        [
            types.RichMessageButton(
                text="💡 𝐇ɪɴᴛ",
                style=enums.ButtonStyle.PRIMARY,
                callback_data=f"game_hint|{puzzle_id}",
            ),
            types.RichMessageButton(
                text="⏭️ 𝐒ᴋɪᴘ",
                style=enums.ButtonStyle.DANGER,
                callback_data=f"game_skip|{puzzle_id}",
            ),
        ],
        [
            types.RichMessageButton(
                text="🆕 𝐍ᴇᴡ 𝐖ᴏʀᴅ",
                style=enums.ButtonStyle.SUCCESS,
                callback_data="game_newword",
            ),
        ]
    ]


async def disable_dead_chat(chat_id: int):
    try:
        DB.execute("UPDATE groups SET is_active = 0, enabled = 0 WHERE chat_id = ?", (chat_id,))
        DB.execute("UPDATE settings SET is_active = 0 WHERE chat_id = ?", (chat_id,))
        DB.execute("DELETE FROM games WHERE chat_id = ?", (chat_id,))
        DB.commit()
    except Exception:
        pass


async def start_game(client: Client, chat_id: int, difficulty: str, message_or_chat):
    if chat_id in ACTIVE_FIGHTS:
        return

    raw_settings = get_settings(chat_id)
    settings = dict(raw_settings) if raw_settings else {}
    if not settings.get("is_active", 1):
        return

    old_game = DB.execute("SELECT message_id FROM games WHERE chat_id=?", (chat_id,)).fetchone()
    if old_game and settings.get("auto_delete") and old_game["message_id"]:
        await safe_delete_and_unpin(client, chat_id, old_game["message_id"])

    DB.execute("DELETE FROM games WHERE chat_id=?", (chat_id,))

    word = choose_word(chat_id, difficulty)
    jumbled = jumble_word(word)
    puzzle_id = random.randint(10000, 99999)
    now = time.time()

    default_timer = 30 if difficulty.lower() == "easy" else 60
    timer_val = int(settings.get(difficulty.lower(), default_timer))

    reward_pts = int(get_global_config(f"points_{difficulty}", 10))
    reward_exp = int(get_global_config(f"exp_{difficulty}", 15))
    hint_limit = int(get_global_config(f"hints_{difficulty}", 3))
    expires = now + timer_val

    DB.execute("""
        INSERT INTO games(chat_id, difficulty, word, puzzle_id, started, expires, message_id, solved)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (chat_id, difficulty, word, puzzle_id, now, expires, 0))
    DB.commit()

    image_obj = make_puzzle_image(jumbled, difficulty, puzzle_id)

    time_str = f"{timer_val}s" if timer_val < 60 else f"{timer_val // 60}m {timer_val % 60}s"
    caption_html = (
        f"<blockquote>🧩 <u><b>JUMBLE #{puzzle_id}</b></u></blockquote>\n\n"
        f"<blockquote>🎯 <b>Difficulty :</b> <code>{difficulty.title()}</code>\n"
        f"⏱️ <b>Time :</b> <code>{time_str}</code>\n"
        f"⭐ <b>Reward :</b> <code>+{reward_pts} Points</code>\n"
        f"⚡ <b>EXP :</b> <code>+{reward_exp} EXP</code>\n"
        f"💡 <b>Hints :</b> <code>{hint_limit}/word</code></blockquote>\n\n"
        f"<blockquote>🔀 <i>Unscramble the letters & type in chat!</i></blockquote>"
    )

    buttons = rich_game_buttons(puzzle_id)

    try:
        sent = await send_jumble_rich(client, chat_id, caption_html, rich_buttons_rows=buttons, photo=image_obj)
        if sent:
            DB.execute("UPDATE games SET message_id=? WHERE chat_id=?", (sent.id, chat_id))
            DB.commit()
            if chat_id < 0:
                await safe_pin_and_clean(client, chat_id, sent.id)
    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserIsBlocked, InputUserDeactivated):
        print(f"[Deactivating Inaccessible Chat]: {chat_id}")
        await disable_dead_chat(chat_id)
        return
    except Exception as e:
        print(f"[start_game Dispatch Error in {chat_id}]: {e}")
        return

    asyncio.create_task(expire_game(client, chat_id, puzzle_id, expires))


async def expire_game(client: Client, chat_id: int, puzzle_id: int, expires: float):
    wait_time = max(0, expires - time.time())
    await asyncio.sleep(wait_time)

    if chat_id in ACTIVE_FIGHTS:
        return

    row = DB.execute("SELECT * FROM games WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id)).fetchone()
    if not row or row["solved"]:
        return

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=? AND puzzle_id=?", (chat_id, puzzle_id))
    DB.commit()

    raw_s = get_settings(chat_id)
    s = dict(raw_s) if raw_s else {}
    if s.get("auto_delete") and row["message_id"] and chat_id < 0:
        await safe_delete_and_unpin(client, chat_id, row["message_id"])

    try:
        expire_caption = (
            f"<blockquote>⏰ <u><b>TIME'S UP!</b></u></blockquote>\n\n"
            f"<blockquote>❌ <b>Nobody solved it.</b>\n"
            f"✅ <b>Answer was :</b> <code>{row['word'].upper()}</code>\n\n"
            f"🔄 <i>Next puzzle starting in 1 second...</i></blockquote>"
        )
        exp_msg = await send_jumble_rich(client, chat_id, expire_caption)
        if s.get("auto_delete") and exp_msg:
            asyncio.create_task(delete_after(exp_msg, 4))
    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserIsBlocked):
        await disable_dead_chat(chat_id)
        return
    except Exception:
        pass

    await asyncio.sleep(1)

    if chat_id not in ACTIVE_FIGHTS:
        s = dict(get_settings(chat_id)) if get_settings(chat_id) else {}
        if s.get("is_active", 1):
            next_diff = s.get("default_diff") or "easy"
            asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))


# ============================================================
# /jumble COMMAND
# ============================================================

@Client.on_message(filters.command(["jumble", "startgame"], prefixes=["/", "!", "."]))
async def start_game_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if message.chat.type != enums.ChatType.PRIVATE:
        if not await check_admin_safe(message.chat, user_id):
            return await message.reply_text("❌ Sirf Group Admins/Owner hi Jumble game activate kar sakte hain.")

    if chat_id in ACTIVE_FIGHTS:
        return await message.reply_text("⚔️ 1v1 Battle chal rahi hai! Current battle khatam hone dein.")

    try:
        DB.execute("""
            INSERT INTO settings (chat_id, is_active, default_diff, easy)
            VALUES (?, 1, 'easy', 30)
            ON CONFLICT(chat_id) DO UPDATE SET
                is_active = 1,
                default_diff = 'easy',
                easy = 30
        """, (chat_id,))
        DB.commit()
    except Exception as e:
        print(f"[Settings Update Error]: {e}")

    active = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if active and time.time() < active["expires"]:
        return await message.reply_text("⚠️ Puzzle already chal raha hai! Check recent message.")

    await message.reply_text(
        "<blockquote>🟢 <b>JUMBLE GAME ACTIVATED!</b>\n\n"
        "🎮 <b>Mode :</b> <code>Easy</code>\n"
        "⏱️ <b>Timer :</b> <code>30 Seconds</code>\n"
        "🚀 <i>Spawning first puzzle now...</i></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

    asyncio.create_task(start_game(client, chat_id, "easy", message.chat))


# ============================================================
# /puzzle & /current COMMAND HANDLER
# ============================================================

@Client.on_message(filters.command(["puzzle", "current", "timer"], prefixes=["/", "!", "."]))
async def puzzle_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_FIGHTS:
        return await message.reply_text("⚔️ 1v1 Battle chal rahi hai! Current battle rounds play karein.")

    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game:
        return await message.reply_text(
            "❌ Abhi koi active puzzle nahi chal raha. <code>/jumble</code> se start karein!",
            parse_mode=enums.ParseMode.HTML
        )

    left = max(0, int(game["expires"] - time.time()))
    diff = game["difficulty"]
    await message.reply_text(
        f"<blockquote>🧩 <b>ACTIVE JUMBLE PUZZLE</b>\n\n"
        f"🎯 <b>Difficulty :</b> <code>{diff.title()}</code>\n"
        f"⏳ <b>Time Left :</b> <code>{left}s</code>\n"
        f"💡 Check pinned/recent message for puzzle image!</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# /skip COMMAND
# ============================================================

@Client.on_message(filters.command(["skip", "next"], prefixes=["/", "!", "."]))
async def skip_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if message.chat.type != enums.ChatType.PRIVATE:
        if not await check_admin_safe(message.chat, user_id):
            return await message.reply_text("❌ Sirf Group Admins hi puzzle skip kar sakte hain.")

    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game:
        return await message.reply_text("❌ Koi active puzzle nahi hai skip karne ke liye.")

    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    raw_s = get_settings(chat_id)
    s = dict(raw_s) if raw_s else {}
    if s.get("auto_delete") and game["message_id"] and chat_id < 0:
        await safe_delete_and_unpin(client, chat_id, game["message_id"])

    skip_caption = (
        f"<blockquote>⏭️ <u><b>PUZZLE SKIPPED!</b></u></blockquote>\n\n"
        f"<blockquote>✅ <b>Word was :</b> <code>{game['word'].upper()}</code>\n"
        f"🔄 <i>Next puzzle starting in 1 second...</i></blockquote>"
    )
    msg = await send_jumble_rich(client, chat_id, skip_caption)
    if s.get("auto_delete") and msg:
        asyncio.create_task(delete_after(msg, 4))

    await asyncio.sleep(1)
    if chat_id not in ACTIVE_FIGHTS and s.get("is_active", 1):
        next_diff = s.get("default_diff") or "easy"
        asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))


# ============================================================
# /end & /stop COMMAND
# ============================================================

@Client.on_message(filters.command(["end", "stop", "stopgame"], prefixes=["/", "!", "."]))
async def end_game_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    if message.chat.type != enums.ChatType.PRIVATE:
        if not await check_admin_safe(message.chat, user_id):
            return await message.reply_text("❌ Sirf Group Admins hi game end kar sakte hain.")

    DB.execute("UPDATE settings SET is_active=0 WHERE chat_id=?", (chat_id,))
    game = DB.execute("SELECT message_id FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
    DB.commit()

    if game and game["message_id"] and chat_id < 0:
        await safe_delete_and_unpin(client, chat_id, game["message_id"])

    await message.reply_text("<blockquote>🛑 <b>Jumble Game has been stopped.</b></blockquote>", parse_mode=enums.ParseMode.HTML)


# ============================================================
# BUTTON CALLBACKS (HINT / SKIP / NEW WORD)
# ============================================================

@Client.on_callback_query(filters.regex(r"^(game_hint|game_skip|game_newword|hint|skip|newword)"))
async def puzzle_buttons_listener(client: Client, query: CallbackQuery):
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    data = query.data.split("|")
    action = data[0]

    if action in ["game_hint", "hint"]:
        ensure_user(query.from_user)
        game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
        if not game:
            return await query.answer("❌ Yeh puzzle expire ya solve ho chuka hai!", show_alert=True)

        puzzle_id = game["puzzle_id"]
        word = game["word"]
        difficulty = game["difficulty"]

        hint_limit = int(get_global_config(f"hints_{difficulty}", 3))
        hint_row = DB.execute("SELECT * FROM puzzle_hints WHERE chat_id=? AND puzzle_id=? AND user_id=?", (chat_id, puzzle_id, user_id)).fetchone()
        hints_used = hint_row["hints_used"] if hint_row else 0
        revealed_indices = [int(i) for i in hint_row["revealed_indices"].split(",") if i] if hint_row else []

        if hints_used >= hint_limit:
            return await query.answer(f"❌ Is puzzle ke aapke {hint_limit} hints pure ho gaye!", show_alert=True)

        avail = [i for i in range(len(word)) if i not in revealed_indices]
        if not avail:
            return await query.answer("❌ Saare letters already open hain!", show_alert=True)

        idx = random.choice(avail)
        revealed_indices.append(idx)
        hints_used += 1

        DB.execute("""
            INSERT INTO puzzle_hints(chat_id, puzzle_id, user_id, hints_used, revealed_indices)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, puzzle_id, user_id) DO UPDATE SET
                hints_used=excluded.hints_used,
                revealed_indices=excluded.revealed_indices
        """, (chat_id, puzzle_id, user_id, hints_used, ",".join(map(str, revealed_indices))))
        DB.commit()

        letter = word[idx].upper()
        return await query.answer(f"💡 Letter #{idx + 1} is: '{letter}' ({hint_limit - hints_used} hints left)", show_alert=True)

    elif action in ["game_skip", "skip"]:
        if query.message.chat.type != enums.ChatType.PRIVATE:
            is_adm = await check_admin_safe(query.message.chat, user_id)
            if not is_adm:
                return await query.answer("❌ Sirf Group Admins hi puzzle skip kar sakte hain!", show_alert=True)

        game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
        if not game:
            await query.answer("🔄 Spawning new puzzle...")
            s = dict(get_settings(chat_id)) if get_settings(chat_id) else {}
            next_diff = s.get("default_diff") or "easy"
            return asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))

        DB.execute("UPDATE games SET solved=1 WHERE chat_id=?", (chat_id,))
        DB.commit()

        raw_s = get_settings(chat_id)
        s = dict(raw_s) if raw_s else {}
        if s.get("auto_delete") and game["message_id"] and chat_id < 0:
            await safe_delete_and_unpin(client, chat_id, game["message_id"])

        await query.answer("⏭️ Puzzle skipped successfully!")

        skip_caption = (
            f"<blockquote>⏭️ <u><b>PUZZLE SKIPPED!</b></u></blockquote>\n\n"
            f"<blockquote>✅ <b>Word was :</b> <code>{game['word'].upper()}</code>\n"
            f"🔄 <i>Next puzzle starting in 1 second...</i></blockquote>"
        )
        msg = await send_jumble_rich(client, chat_id, skip_caption)
        if s.get("auto_delete") and msg:
            asyncio.create_task(delete_after(msg, 4))

        await asyncio.sleep(1)
        if chat_id not in ACTIVE_FIGHTS and s.get("is_active", 1):
            next_diff = s.get("default_diff") or "easy"
            asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))

    elif action in ["game_newword", "newword"]:
        game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
        if game and time.time() <= game["expires"]:
            return await query.answer("❌ Current puzzle chal raha hai! Pehle solve ya skip karein.", show_alert=True)

        await query.answer("🧩 Naya puzzle shuru kiya ja raha hai...")
        raw_s = get_settings(chat_id)
        s = dict(raw_s) if raw_s else {}
        next_diff = s.get("default_diff") or "easy"
        asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))


# ============================================================
# SERVICE PIN NOTIFICATION CLEANER
# ============================================================

@Client.on_message(filters.service & filters.group, group=9)
async def auto_clean_service_pins(client: Client, message: Message):
    if getattr(message, "pinned_message", None):
        try:
            await message.delete()
        except Exception:
            pass