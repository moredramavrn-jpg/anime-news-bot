import os
import re
import feedparser
from groq import Groq
import telebot
import requests

# ===== НАСТРОЙКИ (из секретов GitHub) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ===== ИСТОЧНИК: только Goha.ru =====
RSS_URL = "https://www.goha.ru/rss/anime"

POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

# ===== РАБОТА С ФАЙЛОМ ОПУБЛИКОВАННЫХ ССЫЛОК =====
def load_posted():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(posted_links):
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in posted_links:
            f.write(link + '\n')

# ===== ОЧИСТКА ОТ <think> =====
def clean_thinking(text):
    if '<think>' in text:
        end_idx = text.find('</think>')
        if end_idx != -1:
            return text[end_idx + len('</think>'):].strip()
        else:
            start_idx = text.find('<think>')
            if start_idx != -1:
                return text[start_idx + len('<think>'):].strip()
    return text.strip()

# ===== ГЕНЕРАЦИЯ ПОСТА =====
def generate_post(title, summary):
    prompt = f"""Ты — редактор популярного аниме-канала в Telegram. Тебе дали новость (на русском или английском).
Твоя задача — сделать из неё уникальный пост, который не выглядит как копипаст.

Правила:
1. Перескажи своими словами, сохраняя ключевые факты.
2. Пиши на русском языке, от первого лица (как будто ты сам сообщаешь новость подписчикам).
3. Добавь 2-3 эмодзи и хэштеги #аниме #новости.
4. Длина поста: 3-5 предложений.
5. Выведи ТОЛЬКО готовый пост, без пояснений.

Исходная новость:
Заголовок: {title}
Описание: {summary}

Пост:"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",   # если модель недоступна, замените на "qwen/qwen3.6-27b"
            messages=[
                {"role": "system", "content": "Ты — талантливый копирайтер аниме-канала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
            max_tokens=400
        )
        raw = response.choices[0].message.content.strip()
        cleaned = clean_thinking(raw)
        return cleaned if cleaned else f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"
    except Exception as e:
        print(f"Ошибка генерации: {e}")
        return f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"

# ===== ИЗВЛЕЧЕНИЕ КАРТИНКИ ИЗ RSS =====
def extract_image_url(entry):
    # 1. media:content
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    # 2. media:thumbnail
    if 'media_thumbnail' in entry:
        for media in entry.media_thumbnail:
            if 'url' in media:
                return media['url']
    # 3. enclosure (если тип изображение)
    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if 'href' in enc and enc.get('type', '').startswith('image'):
                return enc['href']
            if 'url' in enc and enc.get('type', '').startswith('image'):
                return enc['url']
    # 4. Поиск <img> в summary или description
    summary = entry.get('summary', '') or entry.get('description', '')
    if summary:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
        if match:
            return match.group(1)
    # 5. Поиск в content (если есть)
    content = entry.get('content', [])
    for c in content:
        if 'value' in c:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c['value'], re.IGNORECASE)
            if match:
                return match.group(1)
    return None

# ===== ОСНОВНАЯ ЛОГИКА =====
def main():
    posted_links = load_posted()
    new_posts = 0

    print(f"Обрабатываю ленту: {RSS_URL}")
    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        print(f"Не удалось получить ленту {RSS_URL}: {e}")
        return

    for entry in feed.entries[:10]:   # берём до 10 последних новостей
        link = entry.get('link', '')
        if not link or link in posted_links:
            continue

        title = entry.get('title', 'Без названия')
        summary = entry.get('summary', '') or entry.get('description', '') or ''
        # Убираем HTML-теги из описания
        clean_summary = re.sub(r'<[^>]+>', '', summary).strip()

        # Генерируем уникальный пост
        post_text = generate_post(title, clean_summary)

        # Пытаемся получить картинку
        image_url = extract_image_url(entry)

        try:
            if image_url:
                # Проверяем доступность картинки
                try:
                    r = requests.head(image_url, timeout=5)
                    if r.status_code == 200:
                        bot.send_photo(CHANNEL_ID, image_url, caption=post_text)
                    else:
                        bot.send_message(CHANNEL_ID, post_text)
                except:
                    bot.send_message(CHANNEL_ID, post_text)
            else:
                bot.send_message(CHANNEL_ID, post_text)

            posted_links.add(link)
            new_posts += 1
            print(f"Опубликовано: {title}")

        except Exception as e:
            print(f"Ошибка отправки для {link}: {e}")

    if new_posts > 0:
        save_posted(posted_links)
        print(f"Сохранено {new_posts} новых ссылок в {POSTED_FILE}")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
