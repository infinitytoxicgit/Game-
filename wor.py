import math
import re
from pyrogram import Client, filters, enums, types
from pyrogram.types import Message, CallbackQuery, InlineKeyboardButton
from config import OWNER_ID
from database import DB
from helpers import is_owner, is_authed
from word_bank import WORDS
from utils.rich import send_jumble_rich, edit_jumble_rich, create_rich_button

WORDS_PER_PAGE = 15
VALID_DIFFS = ("easy", "medium", "hard")

try:
    DB.execute("""
        CREATE TABLE IF NOT EXISTS custom_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            UNIQUE(word, difficulty)
        )
    """)
    DB.commit()
except Exception as e:
    print(f"[Custom Words Table Init]: {e}")


async def can_manage_words(client: Client, message: Message) -> tuple[bool, str]:
    if not message.from_user:
        if message.sender_chat and message.sender_chat.id == message.chat.id:
            return True, ""
        return False, "❌ User info not found."

    user_id = message.from_user.id

    try:
        if int(user_id) == int(OWNER_ID):
            return True, ""
    except Exception:
        pass

    try:
        if is_owner(user_id) or is_authed(user_id):
            return True, ""
    except Exception:
        pass

    if message.chat.type == enums.ChatType.PRIVATE:
        return False, "❌ DM me sirf Bot Owner aur Authorized users words manage kar sakte hain."

    try:
        member = await client.get_chat_member(message.chat.id, user_id)
        if member.status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR):
            return True, ""
        return False, "❌ Sirf Group Admins hi words manage kar sakte hain."
    except Exception:
        return True, ""


def get_words_for_diff(difficulty: str):
    difficulty = difficulty.lower()
    builtin = WORDS.get(difficulty, [])
    try:
        rows = DB.execute("SELECT word FROM custom_words WHERE difficulty=?", (difficulty,)).fetchall()
        custom = [r["word"] if isinstance(r, dict) else r[0] for r in rows]
    except Exception:
        custom = []
    combined = sorted(list(set(builtin + custom)))
    return combined


def build_words_page(difficulty: str, page: int = 1):
    difficulty = difficulty.lower()
    all_words = get_words_for_diff(difficulty)
    total_words = len(all_words)
    total_pages = max(1, math.ceil(total_words / WORDS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * WORDS_PER_PAGE
    page_words = all_words[start_idx : start_idx + WORDS_PER_PAGE]

    lines = []
    for i, w in enumerate(page_words, start=start_idx + 1):
        lines.append(f"<b>{i:02d}.</b> <code>{w.upper()}</code>")

    words_body = "\n".join(lines) if lines else "<i>No words found.</i>"

    caption = (
        f"<blockquote>📚 <u><b>{difficulty.upper()} WORDS BANK (Page {page}/{total_pages})</b></u>\n\n"
        f"Total Words in Database : <b>{total_words}</b>\n"
        f"💡 <i>Tap any word code block to copy instantly!</i></blockquote>\n\n"
        f"<blockquote>{words_body}</blockquote>\n\n"
        "<blockquote>➕ <b>Bulk Add :</b> <code>/addword [mode] word1 word2 word3</code>\n"
        "➖ <b>Bulk Del :</b> <code>/delword [mode] word1 word2 word3</code></blockquote>"
    )

    nav_row = []
    if page > 1:
        nav_row.append(create_rich_button(text="◀️ Prev", callback_data=f"wpage|{difficulty}|{page - 1}"))

    nav_row.append(create_rich_button(text=f"📄 {page}/{total_pages}", callback_data="noop"))

    if page < total_pages:
        nav_row.append(create_rich_button(text="Next ▶️", callback_data=f"wpage|{difficulty}|{page + 1}"))

    tab_row = [
        create_rich_button(text="🟢 Easy" if difficulty == "easy" else "⚪ Easy", callback_data="wpage|easy|1"),
        create_rich_button(text="🟢 Medium" if difficulty == "medium" else "⚪ Medium", callback_data="wpage|medium|1"),
        create_rich_button(text="🟢 Hard" if difficulty == "hard" else "⚪ Hard", callback_data="wpage|hard|1"),
    ]

    close_row = [
        create_rich_button(text="❌ Close Panel", callback_data="wpage_close")
    ]

    buttons = [nav_row, tab_row, close_row]
    return caption, buttons


async def update_words_rich_view(client: Client, chat_id: int, message_id: int, caption: str, buttons: list):
    try:
        await edit_jumble_rich(client, chat_id, message_id, caption, rich_buttons_rows=buttons)
    except Exception:
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception:
            pass
        await send_jumble_rich(client, chat_id, caption, rich_buttons_rows=buttons)


# ============================================================
# WORDS BANK PANEL VIEWER (/word, /words, /wordbank)
# ============================================================

@Client.on_message(filters.command(["word", "words", "wordbank", "wordsbank", "jumblewords"], prefixes=["/", "!", "."]), group=-1)
async def words_panel_cmd(client: Client, message: Message):
    allowed, err_text = await can_manage_words(client, message)
    if not allowed:
        return await message.reply_text(f"<blockquote>{err_text}</blockquote>", parse_mode=enums.ParseMode.HTML)

    caption, buttons = build_words_page("easy", 1)
    await send_jumble_rich(client, message.chat.id, caption, rich_buttons_rows=buttons)


@Client.on_callback_query(filters.regex(r"^(wpage|wpage_close)"))
async def words_pagination_callback(client: Client, query: CallbackQuery):
    if query.data == "wpage_close":
        try:
            await query.message.delete()
        except Exception:
            pass
        return await query.answer("Closed!")

    data = query.data.split("|")
    diff = data[1]
    page = int(data[2])

    caption, buttons = build_words_page(diff, page)
    await query.answer()
    return await update_words_rich_view(client, query.message.chat.id, query.message.id, caption, buttons)


# ============================================================
# BULK ADD WORDS COMMAND (/addword, /addwords)
# ============================================================

@Client.on_message(filters.command(["addword", "addwords"], prefixes=["/", "!", "."]), group=-1)
async def bulk_add_words_cmd(client: Client, message: Message):
    allowed, err_text = await can_manage_words(client, message)
    if not allowed:
        return await message.reply_text(f"<blockquote>{err_text}</blockquote>", parse_mode=enums.ParseMode.HTML)

    raw_text = message.text.strip()
    tokens = raw_text.split()
    if len(tokens) < 3:
        return await message.reply_text(
            "<blockquote>📖 <b>BULK ADD WORDS USAGE :</b>\n\n"
            "<code>/addword [easy|medium|hard] [word1] [word2] [word3] ...</code>\n\n"
            "<b>Example :</b>\n"
            "<code>/addword easy apple kind home ball</code>\n"
            "<code>/addword hard helicopter microscope galaxy</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    diff = tokens[1].lower()
    if diff not in VALID_DIFFS:
        return await message.reply_text(f"❌ Invalid difficulty! Choose from: <code>{', '.join(VALID_DIFFS)}</code>", parse_mode=enums.ParseMode.HTML)

    raw_words_text = " ".join(tokens[2:])
    candidates = re.findall(r"[a-zA-Z]+", raw_words_text)

    if not candidates:
        return await message.reply_text("❌ Koi valid word nahi mila! Only alphabets allowed.")

    builtin = set(w.lower() for w in WORDS.get(diff, []))
    try:
        existing_custom = set(
            (r["word"] if isinstance(r, dict) else r[0]).lower() 
            for r in DB.execute("SELECT word FROM custom_words WHERE difficulty=?", (diff,)).fetchall()
        )
    except Exception:
        existing_custom = set()

    added = []
    skipped = []

    for w in candidates:
        w_clean = w.lower().strip()
        if len(w_clean) < 3:
            continue
        if w_clean in builtin or w_clean in existing_custom or w_clean in added:
            skipped.append(w_clean.upper())
            continue

        try:
            DB.execute("INSERT INTO custom_words (word, difficulty) VALUES (?, ?)", (w_clean, diff))
            added.append(w_clean.upper())
            existing_custom.add(w_clean)
        except Exception:
            pass

    DB.commit()

    copyable_added = " ".join([f"<code>{w}</code>" for w in added]) if added else "<i>None</i>"

    reply_msg = (
        f"<blockquote>✅ <b>BULK ADD SUMMARY ({diff.upper()})</b>\n\n"
        f"🟢 <b>Added ({len(added)}) :</b> {copyable_added}\n"
        f"⚪ <b>Skipped/Duplicate ({len(skipped)})</b>\n\n"
        f"💡 <i>Words have been saved into database successfully!</i></blockquote>"
    )
    await send_jumble_rich(client, message.chat.id, reply_msg)


# ============================================================
# BULK DELETE WORDS COMMAND (/delword, /delwords)
# ============================================================

@Client.on_message(filters.command(["delword", "delwords"], prefixes=["/", "!", "."]), group=-1)
async def bulk_del_words_cmd(client: Client, message: Message):
    allowed, err_text = await can_manage_words(client, message)
    if not allowed:
        return await message.reply_text(f"<blockquote>{err_text}</blockquote>", parse_mode=enums.ParseMode.HTML)

    raw_text = message.text.strip()
    tokens = raw_text.split()
    if len(tokens) < 3:
        return await message.reply_text(
            "<blockquote>🗑️ <b>BULK DELETE WORDS USAGE :</b>\n\n"
            "<code>/delword [easy|medium|hard] [word1] [word2] ...</code>\n\n"
            "<b>Example :</b>\n"
            "<code>/delword easy apple kind home</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    diff = tokens[1].lower()
    if diff not in VALID_DIFFS:
        return await message.reply_text(f"❌ Invalid difficulty! Choose from: <code>{', '.join(VALID_DIFFS)}</code>", parse_mode=enums.ParseMode.HTML)

    raw_words_text = " ".join(tokens[2:])
    candidates = re.findall(r"[a-zA-Z]+", raw_words_text)

    if not candidates:
        return await message.reply_text("❌ Koi word nahi mila!")

    deleted = []
    not_found = []

    for w in candidates:
        w_clean = w.lower().strip()
        try:
            cur = DB.execute("DELETE FROM custom_words WHERE difficulty=? AND word=?", (diff, w_clean))
            if cur.rowcount > 0:
                deleted.append(w_clean.upper())
            else:
                not_found.append(w_clean.upper())
        except Exception:
            not_found.append(w_clean.upper())

    DB.commit()

    copyable_del = " ".join([f"<code>{w}</code>" for w in deleted]) if deleted else "<i>None</i>"

    reply_msg = (
        f"<blockquote>🗑️ <b>BULK DELETE SUMMARY ({diff.upper()})</b>\n\n"
        f"🔴 <b>Deleted ({len(deleted)}) :</b> {copyable_del}\n"
        f"⚪ <b>Not Found in Custom DB ({len(not_found)})</b>\n\n"
        f"💡 <i>(Built-in bank words cannot be deleted via DB)</i></blockquote>"
    )
    await send_jumble_rich(client, message.chat.id, reply_msg)