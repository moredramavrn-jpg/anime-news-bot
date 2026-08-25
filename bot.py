import os
import re
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup

# ===== НАСТРОЙКИ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

RSS_URL = "https://www.goha.ru/rss/anime"
POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def load_posted():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(posted_links):
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in posted_links:
            f.write(link + '\n')

def clean_html(raw_html):
    """Удаляет HTML-теги и лишние пробелы."""
    if not raw_html:
        return ""
    # Удаляем скрипты и стили, если вдруг остались
    soup = BeautifulSoup(raw_html, "lxml")
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text(separator="\n")
    # Убираем пустые строки и лишние пробелы
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def extract_image_url(entry):
    # ... (оставьте вашу реализацию из предыдущего кода)
    # Для примера: если есть media_content, берём первый url
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if 'media_thumbnail' in entry:
        for media in entry.media_thumbnail:
            if 'url' in media:
                return media['url']
    # Добавьте остальные проверки, как раньше
    return None

def fetch_full_text(entry):
    """
    Возвращает полный текст новости.
    1. Пытается взять из entry.content (content:encoded в RSS).
    2. Если нет, загружает страницу по ссылке и извлекает текст.
    3. Иначе возвращает summary.
    """
    # 1. Проверяем content:encoded
    if 'content' in entry:
        content = entry.content[0].get('value', '')
        if content:
            return clean_html(content)

    # 2. Загружаем страницу
    link = entry.get('link')
    if not link:
        return clean_html(entry.get('summary', '') or entry.get('description', ''))

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(link, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')

        # Пытаемся найти основной контент.
        # На Goha.ru, скорее всего, есть <div class="news-content"> или <article>.
        # Подберите селектор под реальную структуру (F12 в браузере).
        content_candidates = [
            soup.select_one('article'),
            soup.select_one('div.news-content'),
            soup.select_one('div.content'),
            soup.select_one('div.news-text'),
            soup.select_one('div.text'),
        ]
        main_content = None
        for candidate in content_candidates:
            if candidate:
                main_content = candidate
                break

        # Если не нашли, берём самый длинный блок с текстом (эвристика)
        if not main_content:
            paragraphs = soup.find_all('p')
            if paragraphs:
                # Ищем родителя с максимальным количеством текста
                best_parent = None
                best_len = 0
                for p in paragraphs:
                    parent = p.find_parent()
                    text_len = len(parent.get_text(strip=True))
                    if text_len > best_len:
                        best_len = text_len
                        best_parent = parent
                if best_parent:
                    main_content = best_parent

        if main_content:
            return clean_html(str(main_content))

        # Если ничего не нашли, возвращаем весь текст страницы (может быть шумно)
        return clean_html(r.text)

    except Exception as e:
        print(f"Ошибка загрузки полного текста {link}: {e}")
        return clean_html(entry.get('summary', '') or entry.get('description', ''))

def format_post(title, full_text, link):
    """Формирует пост: заголовок + полный текст (+ ссылка по желанию)."""
    text = title.strip()
    if full_text:
        text += f"\n\n{full_text}"
    # Ссылку можно добавить, раскомментировав следующую строку:
    # text += f"\n\nИсточник: {link}"
    return text

def main():
    posted_links = load_posted()
    new_posts = 0

    print(f"Обрабатываю ленту: {RSS_URL}")
    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        print(f"Не удалось получить ленту {RSS_URL}: {e}")
        return

    for entry in feed.entries[:10]:
        link = entry.get('link', '')
        if not link or link in posted_links:
            continue

        title = entry.get('title', 'Без названия')
        # Получаем полный текст
        full_text = fetch_full_text(entry)

        # Формируем пост
        post_text = format_post(title, full_text, link)

        # Пытаемся получить картинку
        image_url = extract_image_url(entry)

        try:
            if image_url:
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
