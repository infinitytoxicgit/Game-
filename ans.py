import asyncio
import time
from database import DB, get_settings, get_global_config, ensure_user, get_user
from helpers import (
    clean_answer, 
    get_mention, 
    safe_delete_and_unpin, 
    delete_after, 
    send_log_event, 
    ACTIVE_FIGHTS
)
from plugins.game_core import start_game
from pyrogram import Client, filters, enums, types
from pyrogram.types import Message
from utils.rich import send_jumble_rich

# GROUP=100 RAKHO TAUB COMMANDS (GROUP 0) PEHLE EXECUTE HO SAKEIN
@Client.on_message(filters.text & filters.group, group=100)
async def group_answer_handler(client: Client, message: Message):
    if not message.from_user or not message.text:
        message.continue_propagation()
        return

    txt = message.text.strip()

    # AGAR COMMAND HAI TOH PROPAGATE KARKE NIKLO
    if txt.startswith(("/", "!", ".")):
        message.continue_propagation()
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    cleaned_input = clean_answer(txt)
    if not cleaned_input:
        message.continue_propagation()
        return

    if chat_id in ACTIVE_FIGHTS:
        message.continue_propagation()
        return

    now = time.time()

    game = DB.execute("SELECT * FROM games WHERE chat_id=? AND solved=0", (chat_id,)).fetchone()
    if not game or now > game["expires"]:
        message.continue_propagation()
        return

    if cleaned_input == clean_answer(game["word"]):
        updated = DB.execute("UPDATE games SET solved=1 WHERE chat_id=? AND solved=0", (chat_id,))
        if updated.rowcount != 1:
            message.continue_propagation()
            return
        DB.commit()

        ensure_user(message.from_user)
        u = get_user(user_id)
        u_dict = dict(u) if u else {}

        raw_settings = get_settings(chat_id)
        settings = dict(raw_settings) if raw_settings else {}

        diff = str(game["difficulty"]).lower()

        base_pts = int(get_global_config(f"points_{diff}", 10))
        base_exp = int(get_global_config(f"exp_{diff}", 15))

        has_p_boost = False
        has_e_boost = False
        try:
            powers = DB.execute(
                "SELECT power_type FROM user_powers WHERE user_id=? AND expires_at > ?",
                (user_id, now)
            ).fetchall()
            power_names = [p["power_type"] for p in powers]
            has_p_boost = ("2x_stars" in power_names or "2x_points" in power_names)
            has_e_boost = ("2x_exp" in power_names)
        except Exception:
            pass

        if not has_p_boost and u_dict.get("point_boost_until", 0) > now:
            has_p_boost = True
        if not has_e_boost and u_dict.get("exp_boost_until", 0) > now:
            has_e_boost = True

        pts_reward = base_pts * 2 if has_p_boost else base_pts
        exp_reward = base_exp * 2 if has_e_boost else base_exp

        old_exp = u_dict.get("exp", 0)
        new_exp = old_exp + exp_reward
        old_level = (old_exp // 500) + 1
        new_level = (new_exp // 500) + 1

        new_streak = u_dict.get("streak", 0) + 1
        best = max(new_streak, u_dict.get("best_streak", 0))

        diff_column = "medium_solved"
        if diff == "easy":
            diff_column = "easy_solved"
        elif diff == "hard":
            diff_column = "hard_solved"

        try:
            DB.execute(f"""
                UPDATE users
                SET points = points + ?,
                    stars = stars + ?,
                    exp = exp + ?,
                    solved = solved + 1,
                    {diff_column} = {diff_column} + 1,
                    streak = ?,
                    best_streak = ?
                WHERE user_id = ?
            """, (pts_reward, pts_reward, exp_reward, new_streak, best, user_id))
            DB.commit()
        except Exception as ue:
            print(f"[Users Update Error]: {ue}")

        try:
            DB.execute(
                "INSERT INTO solve_history (user_id, chat_id, points, timestamp) VALUES (?, ?, ?, ?)",
                (user_id, chat_id, pts_reward, now)
            )
            DB.commit()
        except Exception:
            pass

        try:
            asyncio.create_task(send_log_event(client, message.from_user, message.chat, game["word"], txt, pts_reward, diff))
        except Exception:
            pass

        if settings.get("auto_delete") and game["message_id"]:
            await safe_delete_and_unpin(client, chat_id, game["message_id"])

        u_mention = get_mention(message.from_user)
        booster_tags = []
        if has_p_boost:
            booster_tags.append("⭐ 2x Stars")
        if has_e_boost:
            booster_tags.append("⚡ 2x EXP")

        booster_badge = f"\n🔥 <b>Active Boosters :</b> {' | '.join(booster_tags)}" if booster_tags else ""
        lvl_up_text = f"\n🎉 <b>LEVEL UP!</b> Reached <b>Level {new_level}</b>!" if new_level > old_level else ""

        ans_caption = (
            "<blockquote>🎉 <u><b>CORRECT ANSWER!</b></u></blockquote>\n\n"
            f"<blockquote>👤 <b>Solver :</b> {u_mention}\n"
            f"✅ <b>Word :</b> <code>{game['word'].upper()}</code>\n"
            f"⭐ <b>Gains :</b> +{pts_reward} Stars | +{exp_reward} EXP{booster_badge}\n"
            f"🔥 <b>Streak :</b> <code>{new_streak}</code> (Best: {best}){lvl_up_text}\n\n"
            f"🔄 <i>Next puzzle starting in 2 seconds...</i></blockquote>"
        )

        buttons = [
            [
                types.RichMessageButton(
                    text="🛍️ Power Shop",
                    style=enums.ButtonStyle.SUCCESS,
                    callback_data=f"buy_shop|menu|{user_id}",
                ),
                types.RichMessageButton(
                    text="👤 My Stats",
                    style=enums.ButtonStyle.DANGER,
                    callback_data=f"show_my_stats|{user_id}",
                ),
            ]
        ]

        try:
            c_msg = await send_jumble_rich(client, chat_id, ans_caption, buttons)
            if settings.get("auto_delete") and c_msg:
                asyncio.create_task(delete_after(c_msg, 4))
        except Exception as se:
            print(f"[Win Card Error]: {se}")

        await asyncio.sleep(2)
        try:
            if chat_id not in ACTIVE_FIGHTS and settings.get("is_active", 1):
                next_diff = settings.get("default_diff") or "easy"
                asyncio.create_task(start_game(client, chat_id, next_diff, chat_id))
        except Exception as ge:
            print(f"[Spawn Next Error]: {ge}")
    else:
        message.continue_propagation()