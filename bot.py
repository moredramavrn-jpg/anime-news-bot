import os
import re
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup

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
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "lxml")
    for script in soup(["script", "style"]):
        script.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def extract_image_url(entry):
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if 'media_thumbnail' in entry:
        for media in entry.media_thumbnail:
            if 'url' in media:
                return media['url']
    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if 'href' in enc and enc.get('type', '').startswith('image'):
                return enc['href']
            if 'url' in enc and enc.get('type', '').startswith('image'):
                return enc['url']
    summary = entry.get('summary', '') or entry.get('description', '')
    if summary:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
        if match:
            return match.group(1)
    content = entry.get('content', [])
    for c in content:
        if 'value' in c:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c['value'], re.IGNORECASE)
            if match:
                return match.group(1)
    return None

def fetch_full_text(entry):
    if 'content' in entry:
        content = entry.content[0].get('value', '')
        if content:
            return clean_html(content)

    link = entry.get('link')
    if not link:
        return clean_html(entry.get('summary', '') or entry.get('description', ''))

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(link, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')

        selectors = [
            'article',
            'div.news-content',
            'div.content',
            'div.news-text',
            'div.text',
            'div.post-content',
            'div.entry-content',
            'div.article-content',
            'div.news-detail__text',
            'div.b-news__text',
            'div.js-news-text',
            'div.article__text',
            'div.story__text',
            'div.text-content',
            'div.news-item__text',
            'div.detail__text',
            'div.news-full__text'
        ]
        main_content = None
        for selector in selectors:
            candidate = soup.select_one(selector)
            if candidate:
                main_content = candidate
                break

        if not main_content:
            paragraphs = soup.find_all('p')
            if paragraphs:
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
            full_text = clean_html(str(main_content))
        else:
            full_text = clean_html(r.text)

        return full_text

    except Exception as e:
        print(f"Ошибка загрузки полного текста {link}: {e}")
        return clean_html(entry.get('summary', '') or entry.get('description', ''))

def format_post(title, full_text, link):
    text = title.strip()
    if full_text:
        max_len = 3800
        if len(full_text) > max_len:
            full_text = full_text[:max_len] + "...\n\nЧитать полностью: " + link
        else:
            full_text += "\n\nИсточник: " + link
        text += f"\n\n{full_text}"
    else:
        text += f"\n\nИсточник: {link}"
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
        full_text = fetch_full_text(entry)
        post_text = format_post(title, full_text, link)
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
