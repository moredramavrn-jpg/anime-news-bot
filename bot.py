import os
import re
import feedparser
import telebot
import requests

# ===== НАСТРОЙКИ (из секретов GitHub) =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# ===== ИСТОЧНИК: только Goha.ru =====
RSS_URL = "https://www.goha.ru/rss/anime"

POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

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

# ===== ФОРМИРОВАНИЕ ТЕКСТА ПОСТА (без ИИ) =====
def format_post(title, summary, link):
    """
    Собирает сообщение из заголовка и описания.
    Можно добавить ссылку на источник (раскомментируйте строку).
    """
    text = title.strip()
    if summary:
        # Убираем HTML-теги, оставляем чистый текст
        clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
        if clean_summary:
            text += f"\n\n{clean_summary}"
    # Если хотите добавить ссылку на новость, раскомментируйте следующую строку:
    # text += f"\n\nИсточник: {link}"
    return text

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

        # Формируем текст поста (без изменений)
        post_text = format_post(title, summary, link)

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
