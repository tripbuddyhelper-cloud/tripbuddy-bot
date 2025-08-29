import os
import logging
from flask import Flask, request
import telebot
from telebot import types

telebot.logger.setLevel(logging.WARNING)

BOT_TOKEN = os.environ["BOT_TOKEN"]  # ОБЯЗАТЕЛЬНО в Deployment Secrets

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def root():
    return "OK", 200

# webhook доступен и как /webhook, и как /webhook/<token>
@app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
@app.route(f"/webhook/{BOT_TOKEN}", methods=["GET", "POST"], strict_slashes=False)
def webhook():
    if request.method == "GET":
        return "Webhook here", 200
    if "application/json" not in (request.headers.get("content-type") or "").lower():
        return "OK", 200
    try:
        json_str = request.get_data(as_text=True)
        update = types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print("webhook error:", e)
    return "OK", 200

@bot.message_handler(commands=["start", "help"])
def on_start(message: types.Message):
    bot.reply_to(message, "Привет! Я живой 🤖. Напиши что-нибудь — я повторю.")

@bot.message_handler(func=lambda m: True)
def echo_all(message: types.Message):
    bot.send_message(message.chat.id, f"Ты написал: {message.text}")

# Локальная кнопка Run (только в IDE, не в деплое)
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
