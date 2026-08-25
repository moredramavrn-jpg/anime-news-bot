import os
import telebot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

try:
    bot.send_message(CHANNEL_ID, "Тестовое сообщение")
    print("Сообщение отправлено")
except Exception as e:
    print(f"Ошибка: {e}")
