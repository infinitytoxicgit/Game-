import html
import io
import os
import random
from PIL import Image, ImageDraw, ImageFont
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ============================================================
# FONT & IMAGE ASSET HELPERS
# ============================================================

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

def make_puzzle_image(jumbled, mode_tag, puzzle_id, exp_val=0, is_event=False):
    bg_color = "#20122e" if is_event else "#10131a"
    accent = "#ff007f" if is_event else "#00e5ff"

    img = Image.new("RGB", (1200, 650), bg_color)
    draw = ImageDraw.Draw(img)

    title_font = get_font(55)
    small_font = get_font(35)

    text_len = len(jumbled)
    word_font = get_font(85 if text_len <= 7 else 65 if text_len <= 11 else 50 if text_len <= 15 else 38)
    display_text = "   ".join(jumbled) if text_len <= 7 else "  ".join(jumbled) if text_len <= 11 else " ".join(jumbled)

    title_str = "🌟 EVENT MYSTERY PUZZLE" if is_event else "🧩 JUMBLE WORD"
    draw.text((600, 70), title_str, anchor="mm", font=title_font, fill="white")
    draw.text((600, 300), display_text, anchor="mm", font=word_font, fill=accent)

    exp_suffix = f"  •  +{exp_val} EXP" if exp_val > 0 else ""
    draw.text((600, 480), f"{mode_tag.upper()}{exp_suffix}  •  #{puzzle_id}", anchor="mm", font=small_font, fill="#ffffff")
    draw.text((600, 545), "Unscramble the letters to earn Stars & EXP!", anchor="mm", font=small_font, fill="#aaaaaa")

    bio = io.BytesIO()
    bio.name = f"puzzle_{puzzle_id}_{random.randint(100, 999)}.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

# ============================================================
# STRING & UI FORMATTERS
# ============================================================

def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "Expired"
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    return f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"

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

def format_lb_entry(user_id, name, username, is_private):
    clean_name = html.escape(str(name or "Player"))
    if is_private:
        return f"<b>{clean_name}</b>"
    if username:
        return f"<a href='https://t.me/{username}'>{clean_name}</a> (<code>{user_id}</code>)"
    return f"<a href='tg://openmessage?user_id={user_id}'>{clean_name}</a> (<code>{user_id}</code>)"

# ============================================================
# INLINE KEYBOARD MARKUPS
# ============================================================

def normal_game_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 𝐇ɪɴᴛ", callback_data="hint"),
            InlineKeyboardButton("⏭️ 𝐒ᴋɪᴘ", callback_data="skip")
        ],
        [
            InlineKeyboardButton("🆕 𝐍ᴇᴡ 𝐖ᴏʀᴅ", callback_data="newword"),
            InlineKeyboardButton("🛍️ 𝐒ʜᴏᴘ", callback_data="open_shop_btn")
        ]
    ])

def fight_match_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 𝐇ɪɴᴛ", callback_data="fight_hint")]
    ])

def fight_challenge_keyboard(diff="medium", timer=60, is_bet=False):
    accept_text = "✅ 𝐀ᴄᴄᴇᴘᴛ 𝐁ᴇᴛ" if is_bet else "✅ 𝐀ᴄᴄᴇᴘᴛ 𝐂ʜᴀʟʟᴇɴɢᴇ"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{'✅ ' if diff=='easy' else ''}🟢 𝐄ᴀsʏ", callback_data="f_diff_easy"),
            InlineKeyboardButton(f"{'✅ ' if diff=='medium' else ''}🟡 𝐌ᴇᴅɪᴜᴍ", callback_data="f_diff_medium"),
            InlineKeyboardButton(f"{'✅ ' if diff=='hard' else ''}🔴 𝐇ᴀʀᴅ", callback_data="f_diff_hard")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if timer==30 else ''}⏱️ 30s", callback_data="f_time_30"),
            InlineKeyboardButton(f"{'✅ ' if timer==45 else ''}⏱️ 45s", callback_data="f_time_45"),
            InlineKeyboardButton(f"{'✅ ' if timer==60 else ''}⏱️ 60s", callback_data="f_time_60")
        ],
        [
            InlineKeyboardButton(accept_text, callback_data="f_accept"),
            InlineKeyboardButton("❌ 𝐃ᴇᴄʟɪɴᴇ", callback_data="f_decline")
        ]
    ])

def build_timers_keyboard(s):
    easy_val = s["easy"]
    med_val = s["medium"]
    hard_val = s["hard"]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{'✅ ' if easy_val==60 else ''}Easy: 60s", callback_data="set_t_easy_60"),
            InlineKeyboardButton(f"{'✅ ' if easy_val==120 else ''}Easy: 120s", callback_data="set_t_easy_120")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if med_val==180 else ''}Med: 180s", callback_data="set_t_medium_180"),
            InlineKeyboardButton(f"{'✅ ' if med_val==300 else ''}Med: 300s", callback_data="set_t_medium_300")
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if hard_val==300 else ''}Hard: 300s", callback_data="set_t_hard_300"),
            InlineKeyboardButton(f"{'✅ ' if hard_val==600 else ''}Hard: 600s", callback_data="set_t_hard_600")
        ],
        [InlineKeyboardButton("🔙 𝐁ᴀᴄᴋ", callback_data="set_back")]
    ])

def build_settings_keyboard(s):
    cur_diff = s["default_diff"] if "default_diff" in s.keys() else "medium"
    status_btn = InlineKeyboardButton("⏹️ 𝐒ᴛᴏᴘ 𝐆ᴀᴍᴇ", callback_data="set_stop_game") if s["is_active"] else InlineKeyboardButton("▶️ 𝐒ᴛᴀʀᴛ 𝐆ᴀᴍᴇ", callback_data="set_start_game")
    del_btn = InlineKeyboardButton("🗑️ 𝐀ᴜᴛᴏ-𝐃ᴇʟ: 𝐎𝐍", callback_data="set_toggle_autodel") if s["auto_delete"] else InlineKeyboardButton("🗑️ 𝐀ᴜᴛᴏ-𝐃ᴇʟ: 𝐎𝐅𝐅", callback_data="set_toggle_autodel")

    return InlineKeyboardMarkup([
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

def build_mode_selection_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 𝐄ᴀsʏ", callback_data="set_def_easy"),
            InlineKeyboardButton("🟡 𝐌ᴇᴅɪᴜᴍ", callback_data="set_def_medium"),
            InlineKeyboardButton("🔴 𝐇ᴀʀᴅ", callback_data="set_def_hard")
        ],
        [InlineKeyboardButton("🔙 𝐁ᴀᴄᴋ", callback_data="set_back")]
    ])

def build_wizard_target_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Groups Only", callback_data="ev_target_group"),
            InlineKeyboardButton("💬 DMs Only", callback_data="ev_target_dm")
        ],
        [
            InlineKeyboardButton("🌍 Both Groups & DMs", callback_data="ev_target_both")
        ]
    ])

def build_wizard_review_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm & Save Event", callback_data="ev_confirm_save"),
            InlineKeyboardButton("✏️ Edit / Restart", callback_data="ev_restart_wizard")
        ],
        [
            InlineKeyboardButton("❌ Cancel Event", callback_data="ev_cancel_wizard")
        ]
    ])

def build_leaderboard_keyboard(scope_type, chat_id):
    return InlineKeyboardMarkup([
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

def build_word_bank_keyboard(diff, page, total_pages):
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ 𝐏ʀᴇᴠ", callback_data=f"wb_{diff}_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop_page"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("𝐍ᴇxᴛ ➡️", callback_data=f"wb_{diff}_{page + 1}"))

    return InlineKeyboardMarkup([
        nav_row,
        [
            InlineKeyboardButton("🟢 𝐄ᴀsʏ", callback_data="wb_easy_1"),
            InlineKeyboardButton("🟡 𝐌ᴇᴅɪᴜᴍ", callback_data="wb_medium_1"),
            InlineKeyboardButton("🔴 𝐇ᴀʀᴅ", callback_data="wb_hard_1")
        ],
        [
            InlineKeyboardButton("🔙 𝐁ᴀᴄᴋ ᴛᴏ 𝐌ᴇɴᴜ", callback_data="back_to_words_menu"),
            InlineKeyboardButton("❌ 𝐂ʟᴏsᴇ", callback_data="close_panel")
        ]
    ])

def build_broadcast_status_text(total_served, success_count, failed_count, mode_flags, is_complete=False):
    status_icon = "✅" if is_complete else "⏳"
    header = "ʙʀσᴧᴅᴄᴧsᴛ ᴄσϻᴘʟєᴛєᴅ" if is_complete else "ʙʀσᴧᴅᴄᴧsᴛɪηɢ ɪη ᴘʀσɢʀєss"
    return (
        f"<blockquote>{status_icon} <b>{header}</b>\n\n"
        f"✦ <b>Total Targets:</b> <code>{total_served}</code>\n"
        f"✦ <b>Sent Successfully:</b> <code>{success_count}</code>\n"
        f"✦ <b>Failed/Blocked:</b> <code>{failed_count}</code>\n"
        f"✦ <b>Active Flags:</b> <code>{', '.join(mode_flags) if mode_flags else 'Default'}</code></blockquote>"
    )
