# Упрощённая версия без Hugging Face
import os
import re
import html
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup
from telebot import types

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

RSS_URL = "https://www.goha.ru/rss/anime"
POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ... (все функции загрузки, извлечения картинок, видео и т.д., как раньше)

def simple_truncate_by_sentences(text, max_len):
    if len(text) <= max_len:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = ""
    for s in sentences:
        if len(result) + len(s) + 1 > max_len:
            break
        result = (result + " " + s).strip()
    if not result:
        return text[:max_len]
    return result

def format_news_body(text):
    # ... (как было)
    pass

def build_post_html(title, body, tags, date_str):
    # ... (как было)
    pass

def send_post(...):
    # Отправка без HF, просто обрезаем текст
    pass

def main():
    # ... (аналогично, но без HF)
    pass
