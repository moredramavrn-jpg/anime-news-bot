import os
import re
import html
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup

# ===== НАСТРОЙКИ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
HF_API_KEY = os.getenv("HF_API_KEY")

RSS_URL = "https://www.goha.ru/rss/anime"
POSTED_FILE = "posted.txt"

HF_MODEL_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

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

def extract_full_text_from_page(soup):
    if not soup:
        return ""
    main_content = soup.select_one('div.editor-body')
    if not main_content:
        selectors = [
            'article', 'div.news-content', 'div.content', 'div.news-text',
            'div.post-content', 'div.entry-content', 'div.article-content',
            'div.news-detail__text', 'div.b-news__text', 'div.js-news-text',
            'div.article__text', 'div.text-content', 'div.news-item__text',
            'div.detail__text', 'div.news-full__text'
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

def extract_video_url_from_page(soup):
    if not soup:
        return None, False

    yt_tag = soup.select_one('editor-body-youtube')
    if yt_tag and yt_tag.get('url'):
        return yt_tag['url'], True

    iframe = soup.select_one('iframe[src*="youtube.com"], iframe[src*="youtu.be"]')
    if iframe and iframe.get('src'):
        return iframe['src'], True

    video_tag = soup.select_one('video')
    if video_tag:
        src = video_tag.get('src')
        if src:
            return src, False
        source_tag = video_tag.select_one('source')
        if source_tag and source_tag.get('src'):
            return source_tag['src'], False

    og_video = soup.select_one('meta[property="og:video"]')
    if og_video and og_video.get('content'):
        url = og_video['content']
        is_yt = 'youtube.com' in url or 'youtu.be' in url
        return url, is_yt

    yt_link = soup.select_one('a[href*="youtube.com"], a[href*="youtu.be"]')
    if yt_link and yt_link.get('href'):
        return yt_link['href'], True

    return None, False

def fetch_video_info(entry):
    link = entry.get('link')
    if link:
        soup = get_page_soup(link)
        if soup:
            return extract_video_url_from_page(soup)
    return None, False

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

def call_hf_api(prompt):
    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 512,
            "temperature": 0.3,
            "return_full_text": False
        }
    }
    try:
        response = requests.post(HF_MODEL_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            return result[0].get('generated_text', '').strip()
        elif isinstance(result, dict):
            return result.get('generated_text', '').strip()
        else:
            return str(result).strip()
    except Exception as e:
        print(f"Ошибка при обращении к Hugging Face: {e}")
        return None

def smart_truncate(text, max_len):
    if len(text) <= max_len:
        return text
    prompt = f"""Напиши краткий пересказ следующей новости на русском языке. Объём пересказа должен быть не более {max_len} символов. Сохрани все важные факты, имена, названия. Не добавляй ничего от себя.

Новость:
{text}
"""
    shortened = call_hf_api(prompt)
    if shortened and len(shortened) >= 50:
        if len(shortened) > max_len:
            shortened = simple_truncate_by_sentences(shortened, max_len)
        return shortened
    else:
        print("Hugging Face не справился, используем обрезание по предложениям")
        return simple_truncate_by_sentences(text, max_len)

def escape_html(text):
    """Экранирует специальные символы для безопасного HTML."""
    return html.escape(text, quote=False)

def build_post_html(title, body, video_url=None, is_youtube=False):
    """Собирает красиво отформатированный HTML-текст поста."""
    title_esc = escape_html(title)
    body_esc = escape_html(body) if body else ""

    parts = [f"<b>{title_esc}</b>"]

    if body_esc:
        parts.append("──────────")
        parts.append(body_esc)

    if video_url and is_youtube:
        parts.append("")
        parts.append(f'🎬 <a href="{video_url}">Смотреть видео</a>')

    return "\n".join(parts)

def send_post(title, body, image_url, video_url, is_youtube):
    """Отправляет пост в канал с учётом всех элементов."""
    # Для подписи к фото/видео лимит 1024 символа, для обычного сообщения 4096.
    if image_url:
        # Пытаемся отправить фото с подписью
        body_for_caption = smart_truncate(body, 900) if body else ""
        caption = build_post_html(title, body_for_caption, video_url, is_youtube)
        try:
            # Проверяем доступность фото
            r = requests.head(image_url, timeout=5)
            if r.status_code == 200:
                bot.send_photo(CHANNEL_ID, image_url, caption=caption, parse_mode='HTML')
                return
        except Exception as e:
            print(f"Не удалось отправить фото: {e}")

    if video_url and not is_youtube:
        # Прямое видео
        body_for_caption = smart_truncate(body, 900) if body else ""
        caption = build_post_html(title, body_for_caption)
        try:
            bot.send_video(CHANNEL_ID, video_url, caption=caption, parse_mode='HTML')
            return
        except Exception as e:
            print(f"Не удалось отправить видео: {e}")

    # Если фото/видео не отправлены, отправляем обычное сообщение
    body_for_message = smart_truncate(body, 3500) if body else ""
    message = build_post_html(title, body_for_message, video_url, is_youtube)
    bot.send_message(CHANNEL_ID, message, parse_mode='HTML')

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
        video_url, is_youtube = fetch_video_info(entry)

        try:
            send_post(title, full_text, image_url, video_url, is_youtube)

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
