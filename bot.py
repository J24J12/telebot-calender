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
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
DB_PATH = os.environ.get("DB_PATH", "plans.db")

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

pending = {}   # (chat_id, user_id) -> person, waiting for new plan text
editing = {}   # (chat_id, user_id) -> plan_id, waiting for replacement text


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
        "SELECT id, person, plan_text, created_at FROM plans "
        "WHERE chat_id = ? AND created_at LIKE ? "
        "ORDER BY person, created_at",
        (chat_id, f"{prefix}%"),
    ).fetchall()
    conn.close()
    return rows


def delete_plan(plan_id):
    conn = get_db()
    conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


def update_plan_text(plan_id, new_text):
    conn = get_db()
    conn.execute("UPDATE plans SET plan_text = ? WHERE id = ?", (new_text, plan_id))
    conn.commit()
    conn.close()


def get_plan(plan_id):
    conn = get_db()
    row = conn.execute(
        "SELECT id, person, plan_text FROM plans WHERE id = ?", (plan_id,)
    ).fetchone()
    conn.close()
    return row


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def build_plan_picker(chat_id, callback_prefix):
    """Builds an inline keyboard of this month's plans, one button per plan."""
    now = datetime.now(timezone.utc)
    rows = get_plans_for_month(chat_id, now.year, now.month)
    if not rows:
        return None
    markup = types.InlineKeyboardMarkup(row_width=1)
    for plan_id, person, plan_text, _ in rows:
        label = f"{person}: {plan_text}"
        if len(label) > 45:
            label = label[:42] + "..."
        markup.add(types.InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{plan_id}"))
    return markup


# ─────────────────────────────────────────────────────────────
# Bot handlers
# ─────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    bot.reply_to(
        message,
        "👋 Plans bot!\n\n"
        "/addplan — add a new plan\n"
        "/editplan — edit an existing plan\n"
        "/deleteplan — delete a plan\n"
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


@bot.message_handler(commands=["editplan"])
def cmd_editplan(message):
    markup = build_plan_picker(message.chat.id, "edit")
    if markup is None:
        bot.reply_to(message, "No plans saved for this month yet.")
        return
    bot.reply_to(message, "Which plan do you want to edit?", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("edit:"))
def on_edit_selected(call):
    plan_id = int(call.data.split(":", 1)[1])
    key = (call.message.chat.id, call.from_user.id)
    editing[key] = plan_id
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Send the new text for this plan.")


@bot.message_handler(commands=["deleteplan"])
def cmd_deleteplan(message):
    markup = build_plan_picker(message.chat.id, "del")
    if markup is None:
        bot.reply_to(message, "No plans saved for this month yet.")
        return
    bot.reply_to(message, "Which plan do you want to delete?", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("del:"))
def on_delete_selected(call):
    plan_id = int(call.data.split(":", 1)[1])
    plan = get_plan(plan_id)
    if plan is None:
        bot.answer_callback_query(call.id, "Already deleted.")
        return
    delete_plan(plan_id)
    bot.answer_callback_query(call.id, "Deleted")
    bot.edit_message_text(
        f"🗑️ Deleted: {plan[1]}: {plan[2]}",
        call.message.chat.id,
        call.message.message_id,
    )


@bot.message_handler(
    func=lambda message: (message.chat.id, message.from_user.id) in editing
)
def on_edit_text(message):
    key = (message.chat.id, message.from_user.id)
    plan_id = editing.pop(key)
    update_plan_text(plan_id, message.text)
    bot.reply_to(message, f"✅ Updated: {message.text}")


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
    for plan_id, person, plan_text, created_at in rows:
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


set_webhook()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
