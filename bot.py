import os
import re
import feedparser
from groq import Groq
import telebot
import requests

# ===== НАСТРОЙКИ (берутся из секретов GitHub) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ===== ИСТОЧНИКИ RSS =====
RSS_URLS = [
    "https://www.animenewsnetwork.com/news/rss.xml",
    "https://www.crunchyroll.com/news/rss",
    "https://myanimelist.net/rss/news.xml"
]

# Файл для хранения опубликованных ссылок
POSTED_FILE = "posted.txt"

# Инициализация бота и клиента Groq
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

# ===== ФУНКЦИИ ДЛЯ ХРАНЕНИЯ ССЫЛОК =====
def load_posted():
    """Загружает уже опубликованные ссылки из файла."""
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(posted_links):
    """Сохраняет обновлённый список ссылок в файл."""
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in posted_links:
            f.write(link + '\n')

# ===== ГЕНЕРАЦИЯ УНИКАЛЬНОГО ПОСТА ЧЕРЕЗ ИИ =====
def generate_post(title, summary):
    """
    Получает заголовок и описание новости, отправляет их в Groq,
    чтобы получить уникальный пост на русском языке.
    """
    prompt = f"""Ты — редактор популярного аниме-канала в Telegram. Тебе дали новость (на английском или другом языке).
Твоя задача — НЕ просто перевести её, а сделать уникальный пост, который не выглядит как копипаст.

Правила:
1. Внимательно прочитай исходную новость.
2. Перескажи её своими словами, сохраняя все ключевые факты (названия, даты, имена).
3. Избегай дословного перевода и повторения структуры исходного текста.
4. Добавь немного живости: можно выразить лёгкую эмоцию (удивление, радость, ожидание), но не перегибай.
5. Напиши пост на русском языке, от первого лица (как будто ты сам сообщаешь эту новость подписчикам).
6. Обязательно укажи хэштеги #аниме #новости.
7. Используй 2-3 уместных эмодзи.
8. Длина поста: 3-5 предложений (не слишком длинно).

Исходная новость:
Заголовок: {title}
Описание: {summary}

Напиши пост прямо сейчас:"""

    try:
        response = client.chat.completions.create(
            model="qwen/qwen3.6-27b",   # Бесплатная модель на Groq
            messages=[
                {"role": "system", "content": "Ты — талантливый копирайтер и редактор аниме-канала. Ты всегда пишешь уникальные, живые тексты на русском языке."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,      # Высокая вариативность, но не бред
            max_tokens=400
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Ошибка генерации: {e}")
        # Если ИИ недоступен, возвращаем просто заголовок и обрезанное описание
        return f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"

# ===== ИЗВЛЕЧЕНИЕ КАРТИНКИ ИЗ RSS =====
def extract_image_url(entry):
    """Пытается найти URL изображения в RSS-записи."""
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

    for rss_url in RSS_URLS:
        print(f"Обрабатываю ленту: {rss_url}")
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            print(f"Не удалось получить ленту {rss_url}: {e}")
            continue

        # Берём до 5 последних записей из каждой ленты
        for entry in feed.entries[:5]:
            link = entry.get('link', '')
            if not link or link in posted_links:
                continue

            title = entry.get('title', 'Без названия')
            # summary может быть в разных полях
            summary = entry.get('summary', '') or entry.get('description', '') or ''
            # Убираем HTML-теги для передачи в ИИ
            clean_summary = re.sub(r'<[^>]+>', '', summary).strip()

            # Генерируем уникальный пост
            post_text = generate_post(title, clean_summary)

            # Пытаемся получить картинку
            image_url = extract_image_url(entry)

            try:
                if image_url:
                    # Проверяем, что картинка доступна
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

                # Добавляем ссылку в опубликованные
                posted_links.add(link)
                new_posts += 1
                print(f"Опубликовано: {title}")

            except Exception as e:
                print(f"Ошибка отправки для {link}: {e}")

    # Сохраняем обновлённый список опубликованных
    if new_posts > 0:
        save_posted(posted_links)
        print(f"Сохранено {new_posts} новых ссылок в {POSTED_FILE}")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
