import os
import re
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
HF_API_KEY = os.getenv("HF_API_KEY")  # Hugging Face API Key

RSS_URL = "https://www.goha.ru/rss/anime"
POSTED_FILE = "posted.txt"

# Модель Hugging Face для сокращения текста
HF_MODEL_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ===== ФУНКЦИИ ДЛЯ ХРАНЕНИЯ ОПУБЛИКОВАННЫХ ССЫЛОК =====
def load_posted():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(posted_links):
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in posted_links:
            f.write(link + '\n')

# ===== ОЧИСТКА HTML (исправленная) =====
def clean_html(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "lxml")
    for script in soup(["script", "style"]):
        script.decompose()
    # Ищем все абзацы <p> и собираем текст из них, объединяя через пустую строку
    paragraphs = soup.find_all('p')
    if paragraphs:
        lines = []
        for p in paragraphs:
            line = p.get_text(strip=True)
            if line:
                lines.append(line)
        return "\n\n".join(lines)
    # Если <p> нет, используем общий текст с двойными переносами
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)

# ===== ЗАГРУЗКА СТРАНИЦЫ =====
def get_page_soup(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Referer': 'https://www.goha.ru/',
        }
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        print(f"Ошибка загрузки {url}: {e}")
        return None

# ===== ИЗВЛЕЧЕНИЕ ТЕКСТА =====
def extract_full_text_from_page(soup):
    if not soup:
        return ""
    main_content = soup.select_one('div.editor-body')
    if not main_content:
        selectors = [
            'article',
            'div.news-content',
            'div.content',
            'div.news-text',
            'div.post-content',
            'div.entry-content',
            'div.article-content',
            'div.news-detail__text',
            'div.b-news__text',
            'div.js-news-text',
            'div.article__text',
            'div.text-content',
            'div.news-item__text',
            'div.detail__text',
            'div.news-full__text'
        ]
        for selector in selectors:
            main_content = soup.select_one(selector)
            if main_content:
                break
    if main_content:
        return clean_html(str(main_content))
    return ""

def fetch_full_text(entry):
    link = entry.get('link')
    if link:
        soup = get_page_soup(link)
        if soup:
            full_text = extract_full_text_from_page(soup)
            if full_text:
                return full_text
    summary = entry.get('summary', '') or entry.get('description', '')
    if summary:
        return clean_html(summary)
    return ""

# ===== ИЗВЛЕЧЕНИЕ КАРТИНКИ =====
def extract_image_from_page(soup):
    if not soup:
        return None
    img_tag = soup.select_one('div.editor-body-image img')
    if not img_tag:
        img_tag = soup.select_one('div.editor-body img')
    if img_tag and img_tag.get('src'):
        return img_tag['src']
    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get('content'):
        return og_image['content']
    return None

def fetch_image_url(entry):
    link = entry.get('link')
    if link:
        soup = get_page_soup(link)
        if soup:
            image = extract_image_from_page(soup)
            if image:
                return image
    return extract_image_url_from_entry(entry)

def extract_image_url_from_entry(entry):
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

# ===== УМНОЕ СОКРАЩЕНИЕ ТЕКСТА (Hugging Face) =====
def smart_truncate(text, max_len, link):
    if len(text) <= max_len:
        return text

    link_part = "\n\nЧитать полностью: " + link
    available = max_len - len(link_part)

    if available < 100:
        return text[:max_len - len(link_part)] + link_part

    prompt = f"Сократи следующий текст новости до {available} символов, сохранив все ключевые факты и общий смысл. Пиши на русском языке. Не добавляй ничего лишнего.\n\nТекст:\n{text}"

    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": available,
            "temperature": 0.3,
            "return_full_text": False
        }
    }

    try:
        response = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            shortened = result[0].get('generated_text', '').strip()
        elif isinstance(result, dict):
            shortened = result.get('generated_text', '').strip()
        else:
            shortened = str(result).strip()

        if len(shortened) + len(link_part) <= max_len:
            return shortened + link_part
        else:
            return shortened[:max_len - len(link_part)] + link_part
    except Exception as e:
        print(f"Ошибка при сокращении через Hugging Face: {e}")
        return text[:max_len - len(link_part)] + link_part

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

    for entry in feed.entries[:10]:
        link = entry.get('link', '')
        if not link or link in posted_links:
            continue

        title = entry.get('title', 'Без названия')
        full_text = fetch_full_text(entry)
        image_url = fetch_image_url(entry)

        if full_text:
            full_post = title + "\n\n" + full_text
        else:
            full_post = title

        try:
            if image_url:
                try:
                    r = requests.head(image_url, timeout=5)
                    if r.status_code == 200:
                        caption = smart_truncate(full_post, 1024, link)
                        bot.send_photo(CHANNEL_ID, image_url, caption=caption)
                    else:
                        text_only = smart_truncate(full_post, 4000, link)
                        bot.send_message(CHANNEL_ID, text_only)
                except:
                    text_only = smart_truncate(full_post, 4000, link)
                    bot.send_message(CHANNEL_ID, text_only)
            else:
                text_only = smart_truncate(full_post, 4000, link)
                bot.send_message(CHANNEL_ID, text_only)

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
