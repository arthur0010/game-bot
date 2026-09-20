import os
import asyncio
import logging
import threading
import random
import time

from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

logging.basicConfig(
    format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

SESSION_TIMEOUT = 1800
CLEANUP_INTERVAL = 60
TRIGGER_TEXT = "شروع بازی"
BASKETBALL_EMOJI = "🏀"
FOOTBALL_EMOJI = "⚽"

sessions = {}
matches = {}
bb_matches = {}
bb_index = {}
fb_matches = {}
fb_index = {}

app_telegram = None
main_loop = None

RPS_NAMES = {"r": "🪨 سنگ", "p": "📄 کاغذ", "s": "✂️ قیچی"}
WINNING_MOVES = frozenset([("r", "s"), ("p", "r"), ("s", "p")])
RPS_CHOICES = ("r", "p", "s")

MAIN_MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("✂️ سنگ کاغذ قیچی", callback_data="menu_rps")],
    [
        InlineKeyboardButton("🏀 بسکتبال", callback_data="menu_bb"),
        InlineKeyboardButton("⚽ فوتبال", callback_data="menu_fb"),
    ],
    [InlineKeyboardButton("❌ خروج از بازی", callback_data="menu_exit")],
])

RPS_OPPONENT_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🤖 با ربات", callback_data="rps_with_bot")],
    [InlineKeyboardButton("👤 با کاربر", callback_data="rps_with_user")],
    [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")],
])

RPS_PICK_BOT_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🪨 سنگ", callback_data="rps_botpick|r"),
        InlineKeyboardButton("📄 کاغذ", callback_data="rps_botpick|p"),
        InlineKeyboardButton("✂️ قیچی", callback_data="rps_botpick|s"),
    ],
    [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_rps")],
])

BB_THROWS_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1 پرتاب", callback_data="bb_throws|1"),
        InlineKeyboardButton("2 پرتاب", callback_data="bb_throws|2"),
        InlineKeyboardButton("3 پرتاب", callback_data="bb_throws|3"),
    ],
    [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")],
])

FB_THROWS_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("1 شوت", callback_data="fb_throws|1"),
        InlineKeyboardButton("2 شوت", callback_data="fb_throws|2"),
        InlineKeyboardButton("3 شوت", callback_data="fb_throws|3"),
    ],
    [InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back")],
])


def rps_join_kb(mid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✋ من می‌خوام بازی کنم", callback_data=f"rps_join|{mid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"rps_cancel|{mid}")],
    ])


def rps_pick_kb(mid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪨 سنگ", callback_data=f"rps_pick|r|{mid}"),
            InlineKeyboardButton("📄 کاغذ", callback_data=f"rps_pick|p|{mid}"),
            InlineKeyboardButton("✂️ قیچی", callback_data=f"rps_pick|s|{mid}"),
        ],
        [InlineKeyboardButton("❌ پایان بازی", callback_data=f"rps_end|{mid}")],
    ])


def bb_join_kb(mid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✋ من می‌خوام بازی کنم", callback_data=f"bb_join|{mid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"bb_cancel|{mid}")],
    ])


def bb_play_kb(mid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ پایان بازی", callback_data=f"bb_end|{mid}")],
    ])


def fb_join_kb(mid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✋ من می‌خوام بازی کنم", callback_data=f"fb_join|{mid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"fb_cancel|{mid}")],
    ])


def fb_play_kb(mid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ پایان بازی", callback_data=f"fb_end|{mid}")],
    ])


def now_ts():
    return time.time()


def user_display(user):
    return user.first_name or user.username or str(user.id)


def is_expired(item):
    return (now_ts() - item["updated_at"]) > SESSION_TIMEOUT


def throws_display(throws, goal_char="🎯"):
    if not throws:
        return "—"
    return " ".join(goal_char if t else "❌" for t in throws)


def build_match_text(match):
    p1 = match["player1_name"]
    p2 = match["player2_name"] or "—"
    s1 = "✅" if match["p1_choice"] else "⏳"
    s2 = "✅" if match["p2_choice"] else "⏳"

    text = (
        f"✂️ سنگ کاغذ قیچی\n\n"
        f"👤 {p1}: {s1}\n"
        f"👤 {p2}: {s2}\n\n"
        f"📊 امتیاز:  {p1} {match['score1']} — {match['score2']} {p2}\n"
    )

    last_round = match.get("last_round")
    if last_round:
        text += f"\n{last_round}\n"

    text += "\nهر دو نفر انتخاب کنید:"
    return text


def build_bb_text(match):
    p1 = match["player1_name"]
    p2 = match["player2_name"]
    total = match["throws_count"]

    text = (
        f"🏀 بسکتبال | تعداد پرتاب: {total}\n\n"
        f"👤 {p1}: {throws_display(match['p1_throws'], '🎯')}\n"
        f"   گل: {match['score1']} از {len(match['p1_throws'])}\n\n"
        f"👤 {p2}: {throws_display(match['p2_throws'], '🎯')}\n"
        f"   گل: {match['score2']} از {len(match['p2_throws'])}\n"
    )

    last_shot = match.get("last_shot")
    if last_shot:
        text += f"\n━━━━━━━━━━━\n{last_shot}\n━━━━━━━━━━━\n"

    text += "\n🏀 برای پرتاب، روی همین پیام ریپلای کنید و ایموجی بسکتبال بفرستید"
    return text


def build_fb_text(match):
    p1 = match["player1_name"]
    p2 = match["player2_name"]
    total = match["throws_count"]

    text = (
        f"⚽ فوتبال | تعداد شوت: {total}\n\n"
        f"👤 {p1}: {throws_display(match['p1_throws'], '⚽')}\n"
        f"   گل: {match['score1']} از {len(match['p1_throws'])}\n\n"
        f"👤 {p2}: {throws_display(match['p2_throws'], '⚽')}\n"
        f"   گل: {match['score2']} از {len(match['p2_throws'])}\n"
    )

    last_shot = match.get("last_shot")
    if last_shot:
        text += f"\n━━━━━━━━━━━\n{last_shot}\n━━━━━━━━━━━\n"

    text += "\n⚽ برای شوت، روی همین پیام ریپلای کنید و ایموجی فوتبال بفرستید"
    return text


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    me = await context.bot.get_me()
    add_url = f"https://t.me/{me.username}?startgroup=new"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن به گروه", url=add_url)],
    ])

    await update.message.reply_text(
        "🎮 سلام!\n\n"
        "من یک ربات بازی و سرگرمی برای گروه‌ها هستم.\n\n"
        "من رو به گروهت اضافه کن و اونجا بنویس:\n"
        "شروع بازی",
        reply_markup=kb,
    )


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    chat_type = update.effective_chat.type
    if chat_type != "group" and chat_type != "supergroup":
        return
    if msg.text.strip() != TRIGGER_TEXT:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    user_id = user.id
    key = (chat_id, user_id)

    old = sessions.pop(key, None)
    if old:
        try:
            await context.bot.edit_message_text(
                chat_id=old["chat_id"],
                message_id=old["message_id"],
                text="🔄 بازی قبلی بسته شد.",
                reply_markup=None,
            )
        except Exception:
            pass

    sent = await msg.reply_text(
        f"🎮 سلام {user_display(user)}!\n\n"
        f"به ربات بازی خوش آمدید.\n"
        f"یک گزینه انتخاب کنید:",
        reply_markup=MAIN_MENU_KB,
    )

    sessions[key] = {
        "chat_id": chat_id,
        "user_id": user_id,
        "username": user_display(user),
        "message_id": sent.message_id,
        "bot_last_round": None,
        "updated_at": now_ts(),
    }


async def on_basketball_shot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ball_shot(
        update, context, BASKETBALL_EMOJI, bb_matches, bb_index
    )


async def on_football_shot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ball_shot(
        update, context, FOOTBALL_EMOJI, fb_matches, fb_index
    )


async def handle_ball_shot(update, context, emoji, matches_dict, index_dict):
    msg = update.message
    if not msg:
        return

    scored_override = None

    if msg.dice and msg.dice.emoji == emoji:
        scored_override = msg.dice.value >= 4
    elif msg.text and msg.text.strip() == emoji:
        scored_override = None
    else:
        return

    if not msg.reply_to_message:
        return

    chat_type = update.effective_chat.type
    if chat_type != "group" and chat_type != "supergroup":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    reply_to_id = msg.reply_to_message.message_id

    mid = index_dict.get((chat_id, reply_to_id))
    if not mid:
        return

    match = matches_dict.get(mid)
    if not match or match["status"] != "playing":
        return

    if is_expired(match):
        matches_dict.pop(mid, None)
        index_dict.pop((chat_id, reply_to_id), None)
        return

    if user_id != match["player1_id"] and user_id != match["player2_id"]:
        return

    if emoji == BASKETBALL_EMOJI:
        await process_basketball_shot(
            context, match, user_id, scored_override, msg.message_id
        )
    else:
        await process_football_shot(
            context, match, user_id, scored_override, msg.message_id
        )


async def process_basketball_shot(context, match, user_id, scored_override, shot_message_id):
    is_p1 = user_id == match["player1_id"]
    throws = match["p1_throws"] if is_p1 else match["p2_throws"]
    name = match["player1_name"] if is_p1 else match["player2_name"]
    total = match["throws_count"]

    if len(throws) >= total:
        try:
            await context.bot.send_message(
                chat_id=match["chat_id"],
                text=f"⚠️ {name} تعداد پرتاب‌های تعیین شده ({total}) رو کامل کرده!",
                reply_to_message_id=shot_message_id,
            )
        except Exception:
            pass
        return

    if scored_override is None:
        scored = random.random() < 0.5
    else:
        scored = scored_override

    throws.append(scored)
    shot_num = len(throws)

    if scored:
        if is_p1:
            match["score1"] += 1
        else:
            match["score2"] += 1
        last_shot = f"🏀 {name} پرتاب {shot_num}/{total}: گل! 🎯"
    else:
        last_shot = f"❌ {name} پرتاب {shot_num}/{total}: خطا"

    match["last_shot"] = last_shot
    match["updated_at"] = now_ts()

    p1_done = len(match["p1_throws"]) >= total
    p2_done = len(match["p2_throws"]) >= total

    if p1_done and p2_done:
        await finalize_basketball(context, match, last_shot, total)
        return

    try:
        await context.bot.edit_message_text(
            chat_id=match["chat_id"],
            message_id=match["message_id"],
            text=build_bb_text(match),
            reply_markup=bb_play_kb(match["match_id"]),
        )
    except Exception as e:
        logging.exception("bb edit: %s", e)


async def process_football_shot(context, match, user_id, scored_override, shot_message_id):
    is_p1 = user_id == match["player1_id"]
    throws = match["p1_throws"] if is_p1 else match["p2_throws"]
    name = match["player1_name"] if is_p1 else match["player2_name"]
    total = match["throws_count"]

    if len(throws) >= total:
        try:
            await context.bot.send_message(
                chat_id=match["chat_id"],
                text=f"⚠️ {name} تعداد شوت‌های تعیین شده ({total}) رو کامل کرده!",
                reply_to_message_id=shot_message_id,
            )
        except Exception:
            pass
        return

    if scored_override is None:
        scored = random.random() < 0.5
    else:
        scored = scored_override

    throws.append(scored)
    shot_num = len(throws)

    if scored:
        if is_p1:
            match["score1"] += 1
        else:
            match["score2"] += 1
        last_shot = f"⚽ {name} شوت {shot_num}/{total}: گل! 🥅"
    else:
        last_shot = f"❌ {name} شوت {shot_num}/{total}: خطا"

    match["last_shot"] = last_shot
    match["updated_at"] = now_ts()

    p1_done = len(match["p1_throws"]) >= total
    p2_done = len(match["p2_throws"]) >= total

    if p1_done and p2_done:
        await finalize_football(context, match, last_shot, total)
        return

    try:
        await context.bot.edit_message_text(
            chat_id=match["chat_id"],
            message_id=match["message_id"],
            text=build_fb_text(match),
            reply_markup=fb_play_kb(match["match_id"]),
        )
    except Exception as e:
        logging.exception("fb edit: %s", e)


async def finalize_basketball(context, match, last_shot, total):
    if match["score1"] > match["score2"]:
        winner_text = f"🏆 برنده: {match['player1_name']}"
    elif match["score2"] > match["score1"]:
        winner_text = f"🏆 برنده: {match['player2_name']}"
    else:
        winner_text = "🤝 مساوی!"

    text = (
        f"🏀 پایان بازی بسکتبال\n\n"
        f"👤 {match['player1_name']}: {throws_display(match['p1_throws'], '🎯')}\n"
        f"   گل: {match['score1']} از {total}\n\n"
        f"👤 {match['player2_name']}: {throws_display(match['p2_throws'], '🎯')}\n"
        f"   گل: {match['score2']} از {total}\n\n"
        f"━━━━━━━━━━━\n{last_shot}\n━━━━━━━━━━━\n\n"
        f"{winner_text}\n\n"
        f"برای شروع دوباره بنویسید: شروع بازی"
    )

    bb_matches.pop(match["match_id"], None)
    bb_index.pop((match["chat_id"], match["message_id"]), None)

    try:
        await context.bot.edit_message_text(
            chat_id=match["chat_id"],
            message_id=match["message_id"],
            text=text,
            reply_markup=None,
        )
    except Exception as e:
        logging.exception("bb final edit: %s", e)


async def finalize_football(context, match, last_shot, total):
    if match["score1"] > match["score2"]:
        winner_text = f"🏆 برنده: {match['player1_name']}"
    elif match["score2"] > match["score1"]:
        winner_text = f"🏆 برنده: {match['player2_name']}"
    else:
        winner_text = "🤝 مساوی!"

    text = (
        f"⚽ پایان بازی فوتبال\n\n"
        f"👤 {match['player1_name']}: {throws_display(match['p1_throws'], '⚽')}\n"
        f"   گل: {match['score1']} از {total}\n\n"
        f"👤 {match['player2_name']}: {throws_display(match['p2_throws'], '⚽')}\n"
        f"   گل: {match['score2']} از {total}\n\n"
        f"━━━━━━━━━━━\n{last_shot}\n━━━━━━━━━━━\n\n"
        f"{winner_text}\n\n"
        f"برای شروع دوباره بنویسید: شروع بازی"
    )

    fb_matches.pop(match["match_id"], None)
    fb_index.pop((match["chat_id"], match["message_id"]), None)

    try:
        await context.bot.edit_message_text(
            chat_id=match["chat_id"],
            message_id=match["message_id"],
            text=text,
            reply_markup=None,
        )
    except Exception as e:
        logging.exception("fb final edit: %s", e)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    try:
        parts = data.split("|")
        action = parts[0]

        if action == "rps_join":
            await handle_rps_join(q, parts[1])
            return
        if action == "rps_cancel":
            await handle_rps_cancel(q, parts[1])
            return
        if action == "rps_pick":
            await handle_rps_pick(q, parts[2], parts[1])
            return
        if action == "rps_end":
            await handle_rps_end(q, parts[1])
            return
        if action == "rps_botpick":
            await handle_botpick(q, parts[1])
            return
        if action == "bb_throws":
            await handle_bb_throws(q, parts[1])
            return
        if action == "bb_join":
            await handle_bb_join(q, parts[1])
            return
        if action == "bb_cancel":
            await handle_bb_cancel(q, parts[1])
            return
        if action == "bb_end":
            await handle_bb_end(q, parts[1])
            return
        if action == "fb_throws":
            await handle_fb_throws(q, parts[1])
            return
        if action == "fb_join":
            await handle_fb_join(q, parts[1])
            return
        if action == "fb_cancel":
            await handle_fb_cancel(q, parts[1])
            return
        if action == "fb_end":
            await handle_fb_end(q, parts[1])
            return

        await handle_session_callback(
            q, q.message.chat.id, q.from_user.id, q.message.message_id, data
        )
    except Exception as e:
        logging.exception("callback error: %s", e)
        try:
            await q.answer("خطای غیرمنتظره رخ داد.", show_alert=True)
        except Exception:
            pass


async def handle_botpick(q, choice):
    chat_id = q.message.chat.id
    user_id = q.from_user.id
    message_id = q.message.message_id
    key = (chat_id, user_id)
    session = sessions.get(key)

    if not session or session["message_id"] != message_id:
        await q.answer(
            "این بازی برای شما نیست یا منقضی شده.\n"
            "لطفاً خودتان بنویسید: شروع بازی",
            show_alert=True,
        )
        return

    if is_expired(session):
        sessions.pop(key, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    session["updated_at"] = now_ts()
    await q.answer()
    await play_with_bot(q, session, choice)


async def handle_bb_throws(q, throws_str):
    chat_id = q.message.chat.id
    user_id = q.from_user.id
    message_id = q.message.message_id
    key = (chat_id, user_id)
    session = sessions.get(key)

    if not session or session["message_id"] != message_id:
        await q.answer(
            "این بازی برای شما نیست یا منقضی شده.\n"
            "لطفاً خودتان بنویسید: شروع بازی",
            show_alert=True,
        )
        return

    if is_expired(session):
        sessions.pop(key, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    try:
        throws_count = int(throws_str)
        if throws_count not in (1, 2, 3):
            raise ValueError
    except ValueError:
        await q.answer("مقدار نامعتبر.", show_alert=True)
        return

    session["updated_at"] = now_ts()
    await q.answer()

    mid = format(random.randint(0, 0xFFFFFFFF), '08x')
    bb_matches[mid] = {
        "match_id": mid,
        "chat_id": chat_id,
        "message_id": message_id,
        "player1_id": user_id,
        "player1_name": session["username"],
        "player2_id": None,
        "player2_name": None,
        "throws_count": throws_count,
        "p1_throws": [],
        "p2_throws": [],
        "score1": 0,
        "score2": 0,
        "last_shot": None,
        "status": "waiting",
        "updated_at": now_ts(),
    }
    bb_index[(chat_id, message_id)] = mid
    sessions.pop(key, None)
    await q.edit_message_text(
        f"🏀 بسکتبال | تعداد پرتاب: {throws_count}\n\n"
        f"👤 {session['username']} منتظر حریف است...\n\n"
        f"هر کسی می‌خواد بازی کنه روی دکمه زیر بزنه:",
        reply_markup=bb_join_kb(mid),
    )


async def handle_fb_throws(q, throws_str):
    chat_id = q.message.chat.id
    user_id = q.from_user.id
    message_id = q.message.message_id
    key = (chat_id, user_id)
    session = sessions.get(key)

    if not session or session["message_id"] != message_id:
        await q.answer(
            "این بازی برای شما نیست یا منقضی شده.\n"
            "لطفاً خودتان بنویسید: شروع بازی",
            show_alert=True,
        )
        return

    if is_expired(session):
        sessions.pop(key, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    try:
        throws_count = int(throws_str)
        if throws_count not in (1, 2, 3):
            raise ValueError
    except ValueError:
        await q.answer("مقدار نامعتبر.", show_alert=True)
        return

    session["updated_at"] = now_ts()
    await q.answer()

    mid = format(random.randint(0, 0xFFFFFFFF), '08x')
    fb_matches[mid] = {
        "match_id": mid,
        "chat_id": chat_id,
        "message_id": message_id,
        "player1_id": user_id,
        "player1_name": session["username"],
        "player2_id": None,
        "player2_name": None,
        "throws_count": throws_count,
        "p1_throws": [],
        "p2_throws": [],
        "score1": 0,
        "score2": 0,
        "last_shot": None,
        "status": "waiting",
        "updated_at": now_ts(),
    }
    fb_index[(chat_id, message_id)] = mid
    sessions.pop(key, None)
    await q.edit_message_text(
        f"⚽ فوتبال | تعداد شوت: {throws_count}\n\n"
        f"👤 {session['username']} منتظر حریف است...\n\n"
        f"هر کسی می‌خواد بازی کنه روی دکمه زیر بزنه:",
        reply_markup=fb_join_kb(mid),
    )


async def handle_bb_join(q, mid):
    match = bb_matches.get(mid)
    if not match or is_expired(match):
        bb_matches.pop(mid, None)
        bb_index.pop((q.message.chat.id, q.message.message_id), None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if match["status"] != "waiting":
        await q.answer("بازی قبلاً شروع شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id == match["player1_id"]:
        await q.answer("نمی‌تونی با خودت بازی کنی!", show_alert=True)
        return

    match["player2_id"] = user_id
    match["player2_name"] = user_display(q.from_user)
    match["status"] = "playing"
    match["updated_at"] = now_ts()

    await q.answer("🎮 وارد بازی شدی!")

    try:
        await q.edit_message_text(
            build_bb_text(match),
            reply_markup=bb_play_kb(mid),
        )
    except Exception as e:
        logging.exception("bb join edit: %s", e)


async def handle_fb_join(q, mid):
    match = fb_matches.get(mid)
    if not match or is_expired(match):
        fb_matches.pop(mid, None)
        fb_index.pop((q.message.chat.id, q.message.message_id), None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if match["status"] != "waiting":
        await q.answer("بازی قبلاً شروع شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id == match["player1_id"]:
        await q.answer("نمی‌تونی با خودت بازی کنی!", show_alert=True)
        return

    match["player2_id"] = user_id
    match["player2_name"] = user_display(q.from_user)
    match["status"] = "playing"
    match["updated_at"] = now_ts()

    await q.answer("🎮 وارد بازی شدی!")

    try:
        await q.edit_message_text(
            build_fb_text(match),
            reply_markup=fb_play_kb(mid),
        )
    except Exception as e:
        logging.exception("fb join edit: %s", e)


async def handle_bb_cancel(q, mid):
    match = bb_matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if q.from_user.id != match["player1_id"]:
        await q.answer("فقط سازنده بازی می‌تونه لغو کنه.", show_alert=True)
        return

    bb_matches.pop(mid, None)
    bb_index.pop((match["chat_id"], match["message_id"]), None)
    await q.answer()
    try:
        await q.edit_message_text(
            "❌ بازی لغو شد.\nبرای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def handle_fb_cancel(q, mid):
    match = fb_matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if q.from_user.id != match["player1_id"]:
        await q.answer("فقط سازنده بازی می‌تونه لغو کنه.", show_alert=True)
        return

    fb_matches.pop(mid, None)
    fb_index.pop((match["chat_id"], match["message_id"]), None)
    await q.answer()
    try:
        await q.edit_message_text(
            "❌ بازی لغو شد.\nبرای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def handle_bb_end(q, mid):
    match = bb_matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id != match["player1_id"] and user_id != match["player2_id"]:
        await q.answer("شما در این بازی نیستید.", show_alert=True)
        return

    p1 = match["player1_name"]
    p2 = match["player2_name"]

    if match["score1"] > match["score2"]:
        final = f"🏆 برنده نهایی: {p1}"
    elif match["score2"] > match["score1"]:
        final = f"🏆 برنده نهایی: {p2}"
    else:
        final = "🤝 بازی مساوی تمام شد!"

    bb_matches.pop(mid, None)
    bb_index.pop((match["chat_id"], match["message_id"]), None)
    await q.answer()
    try:
        await q.edit_message_text(
            f"🏀 پایان بازی بسکتبال\n\n"
            f"👤 {p1}: {throws_display(match['p1_throws'], '🎯')}\n"
            f"   گل: {match['score1']}\n\n"
            f"👤 {p2}: {throws_display(match['p2_throws'], '🎯')}\n"
            f"   گل: {match['score2']}\n\n"
            f"{final}\n\n"
            f"برای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def handle_fb_end(q, mid):
    match = fb_matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id != match["player1_id"] and user_id != match["player2_id"]:
        await q.answer("شما در این بازی نیستید.", show_alert=True)
        return

    p1 = match["player1_name"]
    p2 = match["player2_name"]

    if match["score1"] > match["score2"]:
        final = f"🏆 برنده نهایی: {p1}"
    elif match["score2"] > match["score1"]:
        final = f"🏆 برنده نهایی: {p2}"
    else:
        final = "🤝 بازی مساوی تمام شد!"

    fb_matches.pop(mid, None)
    fb_index.pop((match["chat_id"], match["message_id"]), None)
    await q.answer()
    try:
        await q.edit_message_text(
            f"⚽ پایان بازی فوتبال\n\n"
            f"👤 {p1}: {throws_display(match['p1_throws'], '⚽')}\n"
            f"   گل: {match['score1']}\n\n"
            f"👤 {p2}: {throws_display(match['p2_throws'], '⚽')}\n"
            f"   گل: {match['score2']}\n\n"
            f"{final}\n\n"
            f"برای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def handle_session_callback(q, chat_id, user_id, message_id, data):
    key = (chat_id, user_id)
    session = sessions.get(key)

    if not session or session["message_id"] != message_id:
        await q.answer(
            "این بازی برای شما نیست یا منقضی شده.\n"
            "لطفاً خودتان بنویسید: شروع بازی",
            show_alert=True,
        )
        return

    if is_expired(session):
        sessions.pop(key, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        try:
            await q.edit_message_text(
                "⏰ بازی منقضی شد. برای شروع دوباره بنویسید: شروع بازی",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    session["updated_at"] = now_ts()
    await q.answer()

    if data == "menu_rps":
        await q.edit_message_text(
            "✂️ سنگ کاغذ قیچی\n\nبا کی می‌خوای بازی کنی؟",
            reply_markup=RPS_OPPONENT_KB,
        )

    elif data == "menu_bb":
        await q.edit_message_text(
            "🏀 بسکتبال\n\n"
            "تعداد پرتاب هر نفر رو انتخاب کن:",
            reply_markup=BB_THROWS_KB,
        )

    elif data == "menu_fb":
        await q.edit_message_text(
            "⚽ فوتبال\n\n"
            "تعداد شوت هر نفر رو انتخاب کن:",
            reply_markup=FB_THROWS_KB,
        )

    elif data == "menu_back":
        await q.edit_message_text(
            f"🎮 سلام {session['username']}!\n\nیک گزینه انتخاب کنید:",
            reply_markup=MAIN_MENU_KB,
        )

    elif data == "menu_exit":
        sessions.pop(key, None)
        await q.edit_message_text(
            "👋 از بازی خارج شدید.\nبرای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )

    elif data == "rps_with_bot":
        session["bot_last_round"] = None
        await q.edit_message_text(
            "✂️ سنگ کاغذ قیچی با ربات\n\nانتخاب کن:",
            reply_markup=RPS_PICK_BOT_KB,
        )

    elif data == "rps_with_user":
        mid = format(random.randint(0, 0xFFFFFFFF), '08x')
        matches[mid] = {
            "match_id": mid,
            "chat_id": chat_id,
            "message_id": message_id,
            "player1_id": user_id,
            "player1_name": session["username"],
            "player2_id": None,
            "player2_name": None,
            "p1_choice": None,
            "p2_choice": None,
            "score1": 0,
            "score2": 0,
            "last_round": None,
            "status": "waiting",
            "updated_at": now_ts(),
        }
        sessions.pop(key, None)
        await q.edit_message_text(
            f"✂️ سنگ کاغذ قیچی\n\n"
            f"👤 {session['username']} منتظر حریف است...\n\n"
            f"هر کسی می‌خواد بازی کنه روی دکمه زیر بزنه:",
            reply_markup=rps_join_kb(mid),
        )


async def play_with_bot(q, session, choice):
    if choice not in RPS_NAMES:
        await q.answer("انتخاب نامعتبر.", show_alert=True)
        return

    bot_choice = random.choice(RPS_CHOICES)

    if choice == bot_choice:
        result = "🤝 مساوی!"
    elif (choice, bot_choice) in WINNING_MOVES:
        result = "🏆 بردی!"
    else:
        result = "😅 باختی!"

    session["bot_last_round"] = (
        f"🕐 دست قبل:\n"
        f"👤 تو: {RPS_NAMES[choice]}\n"
        f"🤖 ربات: {RPS_NAMES[bot_choice]}\n"
        f"{result}"
    )
    session["updated_at"] = now_ts()

    await q.edit_message_text(
        f"✂️ سنگ کاغذ قیچی با ربات\n\n"
        f"{session['bot_last_round']}\n\n"
        f"انتخاب کن:",
        reply_markup=RPS_PICK_BOT_KB,
    )


async def handle_rps_join(q, mid):
    match = matches.get(mid)
    if not match or is_expired(match):
        matches.pop(mid, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if match["status"] != "waiting":
        await q.answer("بازی قبلاً شروع شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id == match["player1_id"]:
        await q.answer("نمی‌تونی با خودت بازی کنی!", show_alert=True)
        return

    match["player2_id"] = user_id
    match["player2_name"] = user_display(q.from_user)
    match["status"] = "playing"
    match["updated_at"] = now_ts()

    await q.answer("🎮 وارد بازی شدی!")

    try:
        await q.edit_message_text(
            build_match_text(match),
            reply_markup=rps_pick_kb(mid),
        )
    except Exception as e:
        logging.exception("edit join: %s", e)


async def handle_rps_cancel(q, mid):
    match = matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if q.from_user.id != match["player1_id"]:
        await q.answer("فقط سازنده بازی می‌تونه لغو کنه.", show_alert=True)
        return

    matches.pop(mid, None)
    await q.answer()
    try:
        await q.edit_message_text(
            "❌ بازی لغو شد.\nبرای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def handle_rps_pick(q, mid, choice):
    match = matches.get(mid)
    if not match or is_expired(match):
        matches.pop(mid, None)
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    if choice not in RPS_NAMES:
        await q.answer("انتخاب نامعتبر.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id == match["player1_id"]:
        if match["p1_choice"]:
            await q.answer("قبلاً انتخاب کردی.", show_alert=True)
            return
        match["p1_choice"] = choice
    elif user_id == match["player2_id"]:
        if match["p2_choice"]:
            await q.answer("قبلاً انتخاب کردی.", show_alert=True)
            return
        match["p2_choice"] = choice
    else:
        await q.answer("شما در این بازی نیستید.", show_alert=True)
        return

    match["updated_at"] = now_ts()
    await q.answer(f"انتخاب شما ثبت شد: {RPS_NAMES[choice]}", show_alert=True)

    if match["p1_choice"] and match["p2_choice"]:
        await show_match_result(q, match)
    else:
        try:
            await q.edit_message_text(
                build_match_text(match),
                reply_markup=rps_pick_kb(mid),
            )
        except Exception as e:
            logging.exception("edit pick: %s", e)


async def show_match_result(q, match):
    c1 = match["p1_choice"]
    c2 = match["p2_choice"]
    p1 = match["player1_name"]
    p2 = match["player2_name"]

    if c1 == c2:
        result_line = "🤝 مساوی"
    elif (c1, c2) in WINNING_MOVES:
        match["score1"] += 1
        result_line = f"🏆 برنده: {p1}"
    else:
        match["score2"] += 1
        result_line = f"🏆 برنده: {p2}"

    match["last_round"] = (
        f"🕐 دست قبل:\n"
        f"{p1}: {RPS_NAMES[c1]}  |  {p2}: {RPS_NAMES[c2]}\n"
        f"{result_line}"
    )

    match["p1_choice"] = None
    match["p2_choice"] = None
    match["updated_at"] = now_ts()

    try:
        await q.edit_message_text(
            build_match_text(match),
            reply_markup=rps_pick_kb(match["match_id"]),
        )
    except Exception as e:
        logging.exception("edit result: %s", e)


async def handle_rps_end(q, mid):
    match = matches.get(mid)
    if not match:
        await q.answer("این بازی منقضی شده.", show_alert=True)
        return

    user_id = q.from_user.id
    if user_id != match["player1_id"] and user_id != match["player2_id"]:
        await q.answer("شما در این بازی نیستید.", show_alert=True)
        return

    p1 = match["player1_name"]
    p2 = match["player2_name"]

    if match["score1"] > match["score2"]:
        final = f"🏆 برنده نهایی: {p1}"
    elif match["score2"] > match["score1"]:
        final = f"🏆 برنده نهایی: {p2}"
    else:
        final = "🤝 بازی مساوی تمام شد!"

    matches.pop(mid, None)
    await q.answer()
    try:
        await q.edit_message_text(
            f"✂️ پایان بازی\n\n"
            f"📊 امتیاز نهایی:\n"
            f"{p1}: {match['score1']}\n"
            f"{p2}: {match['score2']}\n\n"
            f"{final}\n\n"
            f"برای شروع دوباره بنویسید: شروع بازی",
            reply_markup=None,
        )
    except Exception:
        pass


async def cleanup_task():
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            for key in [k for k, s in sessions.items() if is_expired(s)]:
                session = sessions.pop(key, None)
                if not session:
                    continue
                try:
                    await app_telegram.bot.edit_message_text(
                        chat_id=session["chat_id"],
                        message_id=session["message_id"],
                        text="⏰ این بازی به دلیل عدم فعالیت بسته شد.\n"
                             "برای شروع دوباره بنویسید: شروع بازی",
                        reply_markup=None,
                    )
                except Exception:
                    pass

            for mid in [m for m, x in matches.items() if is_expired(x)]:
                match = matches.pop(mid, None)
                if not match:
                    continue
                try:
                    await app_telegram.bot.edit_message_text(
                        chat_id=match["chat_id"],
                        message_id=match["message_id"],
                        text="⏰ این بازی به دلیل عدم فعالیت بسته شد.\n"
                             "برای شروع دوباره بنویسید: شروع بازی",
                        reply_markup=None,
                    )
                except Exception:
                    pass

            for mid in [m for m, x in bb_matches.items() if is_expired(x)]:
                match = bb_matches.pop(mid, None)
                if not match:
                    continue
                bb_index.pop((match["chat_id"], match["message_id"]), None)
                try:
                    await app_telegram.bot.edit_message_text(
                        chat_id=match["chat_id"],
                        message_id=match["message_id"],
                        text="⏰ این بازی به دلیل عدم فعالیت بسته شد.\n"
                             "برای شروع دوباره بنویسید: شروع بازی",
                        reply_markup=None,
                    )
                except Exception:
                    pass

            for mid in [m for m, x in fb_matches.items() if is_expired(x)]:
                match = fb_matches.pop(mid, None)
                if not match:
                    continue
                fb_index.pop((match["chat_id"], match["message_id"]), None)
                try:
                    await app_telegram.bot.edit_message_text(
                        chat_id=match["chat_id"],
                        message_id=match["message_id"],
                        text="⏰ این بازی به دلیل عدم فعالیت بسته شد.\n"
                             "برای شروع دوباره بنویسید: شروع بازی",
                        reply_markup=None,
                    )
                except Exception:
                    pass
        except Exception as e:
            logging.exception("cleanup error: %s", e)


flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
def index():
    return "Game Bot is running", 200


@flask_app.route("/health", methods=["GET"])
def health():
    return "OK", 200


async def process_update_async(update_data):
    if not app_telegram:
        return
    try:
        update = Update.de_json(update_data, app_telegram.bot)
        await app_telegram.process_update(update)
    except Exception as e:
        logging.exception("process update error: %s", e)


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if not app_telegram or main_loop is None:
        return "OK", 200
    try:
        update_data = request.get_json(silent=True)
        if update_data:
            asyncio.run_coroutine_threadsafe(
                process_update_async(update_data), main_loop
            )
    except Exception as e:
        logging.exception("webhook error: %s", e)
    return "OK", 200


async def setup_webhook():
    if not RENDER_URL:
        logging.warning("RENDER_EXTERNAL_URL تنظیم نشده - Webhook ست نشد")
        return

    webhook_url = f"{RENDER_URL}/webhook"
    logging.info("در حال تنظیم Webhook روی: %s", webhook_url)

    try:
        await app_telegram.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.warning("حذف Webhook قدیمی ناموفق: %s", e)

    try:
        await app_telegram.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
            max_connections=40,
        )
    except Exception as e:
        logging.error("تنظیم Webhook ناموفق: %s", e)
        return

    try:
        info = await app_telegram.bot.get_webhook_info()
        logging.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logging.info("وضعیت Webhook:")
        logging.info("  URL: %s", info.url)
        logging.info("  Pending Updates: %s", info.pending_update_count)
        logging.info("  Max Connections: %s", info.max_connections)
        logging.info("  Allowed Updates: %s", info.allowed_updates)
        if info.last_error_date:
            logging.warning("  Last Error: %s", info.last_error_message)
            logging.warning("  Error Date: %s", info.last_error_date)
        else:
            logging.info("  Last Error: بدون خطا")
        logging.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if info.url == webhook_url:
            logging.info("Webhook با موفقیت روی %s تنظیم شد", webhook_url)
        else:
            logging.warning(
                "Webhook ثبت شد اما URL بازگشتی متفاوت است: %s", info.url
            )
    except Exception as e:
        logging.error("دریافت اطلاعات Webhook ناموفق: %s", e)


async def main_async():
    global app_telegram, main_loop

    main_loop = asyncio.get_running_loop()

    app_telegram = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    bb_shot_filter = (
        filters.Dice.BASKETBALL
        | (filters.TEXT & ~filters.COMMAND & filters.Regex(r'^🏀$'))
    )
    fb_shot_filter = (
        filters.Dice.FOOTBALL
        | (filters.TEXT & ~filters.COMMAND & filters.Regex(r'^⚽$'))
    )

    app_telegram.add_handler(CommandHandler("start", on_start))
    app_telegram.add_handler(CallbackQueryHandler(on_callback))
    app_telegram.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'شروع بازی'),
        on_group_message
    ))
    app_telegram.add_handler(MessageHandler(bb_shot_filter, on_basketball_shot))
    app_telegram.add_handler(MessageHandler(fb_shot_filter, on_football_shot))

    await app_telegram.initialize()
    await app_telegram.start()

    asyncio.create_task(cleanup_task())

    await setup_webhook()

    logging.info("ربات روشن شد...")

    while True:
        await asyncio.sleep(60)


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    asyncio.run(main_async())