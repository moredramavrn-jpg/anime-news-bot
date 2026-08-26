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

# Контент-план по дням недели
CONTENT_PLAN = {
    0: "🎯 Топ-5 аниме, которые стоит посмотреть на этой неделе",
    1: "📝 Совет: как выбрать аниме под настроение",
    2: "🏆 Рейтинг: лучшие аниме текущего сезона",
    3: "🎲 Факт дня: интересное из мира аниме",
    4: "🔮 Что посмотреть на выходных: подборка",
    5: "💬 Мнение: почему классика аниме не устаревает",
    6: "📅 Ожидания: анонсы и премьеры следующей недели",
}

EMOJI_POOL = ["🌸", "⚡", "🔥", "💥", "🌟", "🎬", "🍥", "🗡️", "✨", "💫"]

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

def parse_generated_text(raw_text):
    """
    Пытается извлечь заголовок и текст из ответа модели.
    Если формат не соблюдён, использует эвристику:
    заголовок = первая строка, текст = остальное.
    """
    lines = raw_text.strip().split('\n')
    title = None
    body_lines = []

    for line in lines:
        if line.startswith('Заголовок:'):
            title = line.replace('Заголовок:', '').strip()
        elif line.startswith('Текст:'):
            body_lines.append(line.replace('Текст:', '').strip())
        elif not title and not body_lines:
            # Если первые строки не соответствуют формату, считаем первую строку заголовком
            if len(line.strip()) > 3 and not line.startswith('Текст'):
                title = line.strip()
                continue
        else:
            body_lines.append(line.strip())

    body = '\n'.join([l for l in body_lines if l]).strip()

    # Если заголовок не найден, но есть текст – берём первую строку как заголовок
    if not title and body_lines:
        title = body_lines[0]
        body = '\n'.join(body_lines[1:]).strip()

    return title, body

def clean_generated_text(text):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    cleaned = []
    for sent in sentences:
        if sent.strip().endswith('?'):
            continue
        cleaned.append(sent)
    return ' '.join(cleaned)

def add_emoji_to_text(text):
    paragraphs = text.split('\n\n')
    decorated = []
    for i, para in enumerate(paragraphs):
        emoji = EMOJI_POOL[i % len(EMOJI_POOL)]
        decorated.append(f"{emoji} {para.strip()}")
    return '\n\n'.join(decorated)

def has_anime_titles(text):
    return bool(re.search(r'«[^»]+»|"[^"]+"', text))

def generate_content():
    token = get_gigachat_token()
    if not token:
        print("Не удалось получить токен GigaChat")
        return None

    weekday = datetime.utcnow().weekday()
    topic = CONTENT_PLAN.get(weekday, "🎯 Подборка аниме")

    prompt = f"""Составь пост для Telegram-канала об аниме на тему: "{topic}".

Обязательные требования:
- Приведи конкретные названия аниме (минимум 3), желательно в кавычках «».
- Укажи жанры, студии, годы выхода, если уместно.
- Текст должен быть полезным, информативным, без воды.
- Запрещено задавать вопросы читателю.
- Запрещены фразы "И что это...", "Как думаете...", "Непонятно...", "Впрочем...".
- Разбей текст на 3-4 абзаца, каждый по 2-3 предложения.
- Добавь в начало каждого абзаца подходящий эмодзи.
- Напиши на русском языке.

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
                    {"role": "system", "content": "Ты — опытный редактор аниме-канала. Ты всегда пишешь конкретно, с эмодзи, без риторических вопросов."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1000
            },
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        generated_text = data["choices"][0]["message"]["content"].strip()

        # Логируем сырой ответ для диагностики
        print("=== RAW GigaChat Response ===")
        print(generated_text)
        print("============================")

        title, body = parse_generated_text(generated_text)

        if title and body:
            body = clean_generated_text(body)
            if not has_anime_titles(body):
                print("В сгенерированном тексте нет конкретных названий, пробуем ещё раз")
                return None
            if not re.search(r'[🎯📝🏆🎲🔮💬📅]', body):
                body = add_emoji_to_text(body)
            return title, body
        else:
            print("Не удалось распознать результат GigaChat")
            return None
    except Exception as e:
        print(f"Ошибка генерации контента: {e}")
        return None

def send_content_post(title, body):
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
