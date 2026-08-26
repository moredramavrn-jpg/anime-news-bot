import os
import re
import html
import uuid
import time
import urllib3
import telebot
import requests
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

# Контент-план по дням недели (0 = понедельник, 6 = воскресенье)
CONTENT_PLAN = {
    0: "🎯 Топ-5 аниме, которые стоит посмотреть на этой неделе",
    1: "📝 Совет: как выбрать аниме под настроение",
    2: "🏆 Рейтинг: лучшие аниме текущего сезона",
    3: "🎲 Факт дня: интересное из мира аниме",
    4: "🔮 Что посмотреть на выходных: подборка",
    5: "💬 Мнение: почему классика аниме не устаревает",
    6: "📅 Ожидания: анонсы и премьеры следующей недели",
}

def get_gigachat_token():
    global gigachat_access_token, gigachat_token_expires_at

    if gigachat_access_token and time.time() < gigachat_token_expires_at - 30:
        return gigachat_access_token

    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_AUTHORIZATION_KEY}"
    }
    data = {"scope": "GIGACHAT_API_PERS"}
    try:
        r = requests.post(url, headers=headers, data=data, timeout=15, verify=False)
        r.raise_for_status()
        token_data = r.json()
        gigachat_access_token = token_data.get("access_token")
        expires_at = token_data.get("expires_at")
        if expires_at:
            gigachat_token_expires_at = expires_at / 1000 if expires_at > 10**12 else expires_at
        else:
            gigachat_token_expires_at = time.time() + 1800
        return gigachat_access_token
    except Exception as e:
        print(f"Ошибка получения токена GigaChat: {e}")
        return None

def generate_content():
    """Генерирует пост на основе дня недели."""
    token = get_gigachat_token()
    if not token:
        print("Не удалось получить токен GigaChat")
        return None

    # Определяем тему по текущему дню недели
    weekday = datetime.utcnow().weekday()
    topic = CONTENT_PLAN.get(weekday, "🎯 Подборка аниме")

    prompt = f"""Составь интересный пост для Telegram-канала об аниме на тему: "{topic}".
Пост должен быть:
- Уникальным, без дословного копирования из интернета.
- Разбит на 2-3 абзаца, каждый по 2-3 предложения.
- Написан живым, дружелюбным языком.
- В конце добавь хэштеги #аниме #новости #подборка (или соответствующие теме).
- Не используй вводные слова-рассуждения и вопросы.
Выведи результат строго в формате:
Заголовок: <заголовок поста>
Текст: <текст поста>
"""
    try:
        response = requests.post(
            "https://api.giga.chat/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-ID": str(uuid.uuid4()),
                "User-Agent": "AnimeContentBot/1.0"
            },
            json={
                "model": "GigaChat-3-Ultra",
                "messages": [
                    {"role": "system", "content": "Ты — креативный редактор аниме-канала."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.8,
                "max_tokens": 800
            },
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        generated_text = data["choices"][0]["message"]["content"].strip()

        title = ""
        body = ""
        for line in generated_text.split('\n'):
            line = line.strip()
            if line.startswith('Заголовок:'):
                title = line.replace('Заголовок:', '').strip()
            elif line.startswith('Текст:'):
                body = line.replace('Текст:', '').strip()

        if title and body:
            return title, body
        else:
            print("Не удалось распознать результат GigaChat")
            return None
    except Exception as e:
        print(f"Ошибка генерации контента: {e}")
        return None

def send_content_post(title, body):
    """Форматирует и отправляет пост в канал."""
    message = f"✨ <b>{html.escape(title)}</b>\n\n{body}\n\n#аниме #новости"
    try:
        bot.send_message(CHANNEL_ID, message, parse_mode='HTML', disable_web_page_preview=True)
        print("Контент-пост опубликован.")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def main():
    result = generate_content()
    if result:
        send_content_post(*result)
    else:
        print("Не удалось сгенерировать контент.")

if __name__ == "__main__":
    main()
