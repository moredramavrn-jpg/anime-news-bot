import os
import re
import html
import feedparser
import telebot
import requests
from bs4 import BeautifulSoup
from telebot import types

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

def fetch_image_url(entry, soup=None):
    """Получает URL картинки. Если передан soup, использует его, иначе загружает страницу."""
    if soup is None:
        link = entry.get('link')
        if link:
            soup = get_page_soup(link)
    if soup:
        image = extract_image_from_page(soup)
        if image:
            return image
    # Fallback на RSS
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

def fetch_video_info(entry, soup=None):
    """Получает информацию о видео. Если передан soup, использует его."""
    if soup is None:
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

def format_news_body(text):
    if not text:
        return ""
    text = re.sub(r'\n(?!\n)', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text).strip()

    if '\n\n' in text:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    else:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if len(sentences) <= 1:
            paragraphs = [text]
        else:
            paragraphs = []
            current = []
            for sent in sentences:
                current.append(sent)
                if len(current) == 2:
                    paragraphs.append(" ".join(current))
                    current = []
            if current:
                paragraphs.append(" ".join(current))

    def bold_quotes(s):
        return re.sub(r'«[^»]+»', lambda m: f"<b>{m.group(0)}</b>", s)

    paragraphs = [bold_quotes(p) for p in paragraphs]

    if len(paragraphs) > 1:
        paragraphs[0] = f"<i>{paragraphs[0]}</i>"

    return "\n\n".join(paragraphs)

def escape_html(text):
    return html.escape(text, quote=False)

def make_hashtag(text):
    """Преобразует текст в CamelCase-хэштег без пробелов и спецсимволов."""
    words = text.strip().split()
    clean_words = []
    for w in words:
        # Оставляем только буквы и цифры
        clean_w = re.sub(r'[^\w]', '', w, flags=re.UNICODE)
        if clean_w:
            # Первая буква заглавная, остальные как есть
            clean_words.append(clean_w[0].upper() + clean_w[1:] if len(clean_w) > 1 else clean_w.upper())
    if not clean_words:
        return None
    return '#' + ''.join(clean_words)

def extract_title_hashtag(title):
    """Извлекает название аниме из заголовка и делает из него хэштег в CamelCase."""
    # Ищем текст в русских кавычках « »
    match = re.search(r'«([^»]+)»', title)
    if not match:
        # Ищем в кавычках ""
        match = re.search(r'"([^"]+)"', title)
    if match:
        anime_name = match.group(1).strip()
        return make_hashtag(anime_name)
    return None

def build_post_html(title, body):
    title_esc = escape_html(title)
    body_formatted = format_news_body(body) if body else ""

    parts = [f"<b>{title_esc}</b>"]

    if body_formatted:
        parts.append("──────────")
        parts.append(body_formatted)

    hashtags = ["#аниме", "#новости"]
    title_tag = extract_title_hashtag(title)
    if title_tag and title_tag not in hashtags:
        hashtags.append(title_tag)

    parts.append("")
    parts.append(" ".join(hashtags))

    return "\n".join(parts)

def send_post(title, body, link, image_url, video_url, is_youtube):
    message_text = build_post_html(title, body)

    keyboard = None
    if video_url and is_youtube:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("🎬 Смотреть видео", url=video_url))

    if video_url and not is_youtube:
        try:
            bot.send_video(CHANNEL_ID, video_url, caption=message_text[:1024], parse_mode='HTML', reply_markup=keyboard)
            return
        except Exception as e:
            print(f"Не удалось отправить видео: {e}")

    if image_url:
        try:
            r = requests.head(image_url, timeout=5)
            if r.status_code == 200:
                short_caption = message_text[:1000]
                bot.send_photo(CHANNEL_ID, image_url, caption=short_caption, parse_mode='HTML', reply_markup=keyboard)
                return
        except Exception as e:
            print(f"Не удалось отправить фото: {e}")

    bot.send_message(CHANNEL_ID, message_text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=keyboard)

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

        # Загружаем страницу один раз (если возможно)
        soup = get_page_soup(link) if link else None

        # Получаем полный текст (используя soup, если он есть)
        full_text = extract_full_text_from_page(soup) if soup else fetch_full_text(entry)
        # Получаем картинку (используя soup, если он есть)
        image_url = fetch_image_url(entry, soup)
        # Получаем видео (используя soup, если он есть)
        video_url, is_youtube = fetch_video_info(entry, soup)

        try:
            send_post(title, full_text, link, image_url, video_url, is_youtube)
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
