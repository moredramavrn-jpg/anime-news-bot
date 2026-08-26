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

CONTENT_PLAN = {
    0: "🎯 Топ-5 аниме, которые стоит посмотреть на этой неделе",
    1: "📝 Совет: как выбрать аниме под настроение",
    2: "🏆 Рейтинг: популярные аниме последних лет",
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
    lines = raw_text.strip().split('\n')
    title = None
    body_lines = []

    for line in lines:
        if line.startswith('Заголовок:'):
            title = line.replace('Заголовок:', '').strip()
        elif line.startswith('Текст:'):
            body_lines.append(line.replace('Текст:', '').strip())
        elif not title and not body_lines and len(line.strip()) > 3:
            title = line.strip()
        else:
            body_lines.append(line.strip())

    body = '\n'.join([l for l in body_lines if l]).strip()
    if not title and body_lines:
        title = body_lines[0]
        body = '\n'.join(body_lines[1:]).strip()

    return title, body

def normalize_rating_text(text):
    """Исправляет разрывы строк в рейтинге: '4. ... 5.' объединяются."""
    lines = text.split('\n')
    fixed = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Если строка заканчивается номером с точкой и следующая существует
        if re.search(r'\d+\.\s*$', line) and i + 1 < len(lines):
            combined = line + ' ' + lines[i+1].strip()
            fixed.append(combined)
            i += 2
        else:
            fixed.append(line)
            i += 1
    return '\n'.join(fixed)

def wrap_titles_in_quotes(text):
    """
    Если после медали/номера идёт название без кавычек до тире,
    оборачиваем его в «».
    """
    lines = text.split('\n')
    wrapped = []
    for line in lines:
        # Паттерн: (🥇|🥈|🥉|4.|5.) Название — описание
        m = re.match(r'^((?:🥇|🥈|🥉|\d+\.)\s*)([^—]+)(—.*)?$', line)
        if m:
            prefix = m.group(1)
            title_part = m.group(2).strip()
            rest = m.group(3) or ""
            # Если название уже в кавычках, не трогаем
            if not (title_part.startswith('«') and title_part.endswith('»')):
                title_part = f"«{title_part}»"
            wrapped.append(f"{prefix}{title_part} {rest}".strip())
        else:
            wrapped.append(line)
    return '\n'.join(wrapped)

def has_anime_titles(text):
    """Проверяет, есть ли в тексте названия в кавычках или структура рейтинга."""
    if re.search(r'«[^»]+»|"[^"]+"', text):
        return True
    # Альтернативная проверка: есть строки вида "🥇 Название — описание"
    if re.search(r'(?:🥇|🥈|🥉|\d+\.)\s*.+?—', text):
        return True
    return False

def split_into_paragraphs(text, sentences_per_par=2):
    text = re.sub(r'\s*\n\s*', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) <= sentences_per_par:
        return text

    paragraphs = []
    current = []
    for sent in sentences:
        current.append(sent)
        if len(current) == sentences_per_par:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))

    return '\n\n'.join(paragraphs)

def add_emoji_to_paragraphs(text):
    paragraphs = text.split('\n\n')
    decorated = []
    for i, para in enumerate(paragraphs):
        emoji = EMOJI_POOL[i % len(EMOJI_POOL)]
        if re.match(r'^[\U0001F300-\U0001FAFF]', para):
            decorated.append(para)
        else:
            decorated.append(f"{emoji} {para.strip()}")
    return '\n\n'.join(decorated)

def generate_content():
    token = get_gigachat_token()
    if not token:
        print("Не удалось получить токен GigaChat")
        return None

    weekday = datetime.now().weekday()
    topic = CONTENT_PLAN.get(weekday, "🎯 Подборка аниме")

    if "Рейтинг" in topic:
        system_msg = "Ты — редактор аниме-канала. Ты составляешь рейтинги только из реально существующих аниме."
        prompt = f"""Составь рейтинг из 5 популярных аниме, которые действительно существуют и широко известны.
Формат строго:
🥇 «Название аниме» — краткое описание (1-2 предложения).
🥈 «Название аниме» — краткое описание.
🥉 «Название аниме» — краткое описание.
4. «Название аниме» — краткое описание.
5. «Название аниме» — краткое описание.

Каждый пункт должен быть отдельным абзацем.
Названия аниме обязательно заключай в кавычки «».
Не выдумывай названия. Используй только реальные аниме.
Не добавляй лишних эмодзи, кроме медалей и номеров.
Не задавай вопросы, не пиши вводные слова.
Напиши на русском языке.

Выведи результат строго в формате:
Заголовок: <заголовок поста>
Текст: <текст рейтинга>
"""
    else:
        system_msg = "Ты — опытный редактор аниме-канала. Ты пишешь конкретно, с эмодзи, без риторических вопросов и без выдуманных дат."
        prompt = f"""Составь пост для Telegram-канала об аниме на тему: "{topic}".

Обязательные требования:
- Приведи конкретные названия аниме (минимум 3), желательно в кавычках «».
- Укажи жанры, студии, годы выхода ТОЛЬКО если ты абсолютно уверен в их точности.
- НЕ УКАЗЫВАЙ даты выхода или годы, если сомневаешься.
- Не выдумывай факты.
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
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1200 if "Рейтинг" in topic else 1000
            },
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        generated_text = data["choices"][0]["message"]["content"].strip()

        print("=== RAW GigaChat Response ===")
        print(generated_text)
        print("============================")

        title, body = parse_generated_text(generated_text)

        if not title or not body:
            print("Не удалось распознать результат GigaChat")
            return None

        if "Рейтинг" in title:
            body = normalize_rating_text(body)
            # Оборачиваем названия в кавычки, если они отсутствуют
            if not re.search(r'«[^»]+»|"[^"]+"', body):
                body = wrap_titles_in_quotes(body)
        else:
            body = split_into_paragraphs(body)
            body = add_emoji_to_paragraphs(body)

        if not has_anime_titles(body):
            print("В сгенерированном тексте нет конкретных названий")
            return None

        return title, body

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
