import os
import sqlite3
from datetime import datetime, timezone

import telebot
from telebot import types
from flask import Flask, request

# ─────────────────────────────────────────────────────────────
# EDIT THIS: your fixed list of names for the group
# ─────────────────────────────────────────────────────────────
PEOPLE = [
    "Jeremy",
    "Yuan Yuan"
]

# ─────────────────────────────────────────────────────────────
# Config (set these as environment variables on your host)
# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]          # from @BotFather
WEBHOOK_URL = os.environ["WEBHOOK_URL"]      # e.g. https://your-app.onrender.com
DB_PATH = os.environ.get("DB_PATH", "plans.db")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)

# in-memory state: tracks who is mid-flow (chat_id, user_id) -> selected person
pending = {}


# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            person TEXT NOT NULL,
            plan_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def add_plan(chat_id, person, plan_text):
    conn = get_db()
    conn.execute(
        "INSERT INTO plans (chat_id, person, plan_text, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, person, plan_text, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_plans_for_month(chat_id, year, month):
    conn = get_db()
    prefix = f"{year:04d}-{month:02d}"
    rows = conn.execute(
        "SELECT person, plan_text, created_at FROM plans "
        "WHERE chat_id = ? AND created_at LIKE ? "
        "ORDER BY person, created_at",
        (chat_id, f"{prefix}%"),
    ).fetchall()
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────
# Bot handlers
# ─────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    bot.reply_to(
        message,
        "👋 Plans bot!\n\n"
        "/addplan — add a new plan (pick a person, then type the plan)\n"
        "/plans — show all plans for this month",
    )


@bot.message_handler(commands=["addplan"])
def cmd_addplan(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(name, callback_data=f"who:{name}") for name in PEOPLE]
    markup.add(*buttons)
    bot.reply_to(message, "Who is this plan for?", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("who:"))
def on_person_selected(call):
    person = call.data.split(":", 1)[1]
    key = (call.message.chat.id, call.from_user.id)
    pending[key] = person
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"Got it — {person}. What's the plan?")


@bot.message_handler(
    func=lambda message: (message.chat.id, message.from_user.id) in pending
)
def on_plan_text(message):
    key = (message.chat.id, message.from_user.id)
    person = pending.pop(key)
    add_plan(message.chat.id, person, message.text)
    bot.reply_to(message, f"✅ Saved for {person}: {message.text}")


@bot.message_handler(commands=["plans"])
def cmd_plans(message):
    now = datetime.now(timezone.utc)
    rows = get_plans_for_month(message.chat.id, now.year, now.month)

    if not rows:
        bot.reply_to(message, "No plans saved for this month yet.")
        return

    by_person = {}
    for person, plan_text, created_at in rows:
        by_person.setdefault(person, []).append(plan_text)

    lines = [f"📅 Plans for {now.strftime('%B %Y')}:\n"]
    for person, plan_list in by_person.items():
        lines.append(f"*{person}*")
        for p in plan_list:
            lines.append(f"  • {p}")
        lines.append("")

    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# Flask webhook routes
# ─────────────────────────────────────────────────────────────
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "Bot is running", 200


def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}")


# Set the webhook as soon as this module is imported, so it works whether
# it's started directly (python bot.py) or via gunicorn (which imports bot:app).
set_webhook()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
