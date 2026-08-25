import os
import re
import html
import io
import feedparser
import telebot
import requests
import yt_dlp
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from telebot import types

# ===== НАСТРОЙКИ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
HF_API_KEY = os.getenv("HF_API_KEY")

RSS_URLS = [
    "https://www.goha.ru/rss/anime",
    "https://kg-portal.ru/rss/news_anime.rss"
]

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

def make_absolute(url, base_domain):
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/'):
        return urljoin(base_domain, url)
    return url

def get_page_soup(url):
    try:
        domain = urlparse(url).netloc
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Referer': f'https://{domain}/',
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

    main_content = soup.select_one('div.editor-body')  # Goha.ru
    if not main_content:
        main_content = soup.select_one('div.news_text')  # КГ-Портал

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

def extract_image_from_page(soup, page_url=None):
    if not soup:
        return None

    selectors = [
        'div.editor-body-image img',      # Goha.ru
        'div.editor-body img',
        'div.news_cover_center img',      # КГ-Портал
        'div.news_text img',
        'div.news_box img',
        'article img',
        'div.news_image img',
        'div.article_image img',
        'div.full_news img',
        'div.news_content img',
        'div.news-full__text img',
    ]

    for selector in selectors:
        img_tag = soup.select_one(selector)
        if img_tag:
            src = (img_tag.get('src') or img_tag.get('data-src') or
                   img_tag.get('data-original') or img_tag.get('data-lazy-src'))
            if src:
                return make_absolute(src, page_url or 'https://kg-portal.ru')

    news_text = soup.select_one('div.news_text')
    if news_text:
        for img in news_text.find_all('img'):
            src = (img.get('src') or img.get('data-src') or
                   img.get('data-original') or img.get('data-lazy-src'))
            if src:
                return make_absolute(src, page_url or 'https://kg-portal.ru')

    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get('content'):
        return make_absolute(og_image['content'], page_url or 'https://kg-portal.ru')

    for img in soup.find_all('img'):
        src = (img.get('src') or img.get('data-src') or
               img.get('data-original') or img.get('data-lazy-src'))
        if src and re.search(r'\.(jpg|jpeg|png|webp)(\?.*)?$', src, re.IGNORECASE):
            return make_absolute(src, page_url or 'https://kg-portal.ru')

    return None

def fetch_image_url(entry, soup=None):
    link = entry.get('link')
    if soup is None and link:
        soup = get_page_soup(link)

    if soup:
        image = extract_image_from_page(soup, link)
        if image:
            return image

    image = extract_image_url_from_entry(entry)
    if image:
        return image

    print(f"Картинка не найдена для {link}")
    return None

def extract_image_url_from_entry(entry):
    base_domain = 'https://www.goha.ru'
    if 'link' in entry:
        link = entry.get('link', '')
        if 'kg-portal.ru' in link:
            base_domain = 'https://kg-portal.ru'

    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media:
                return make_absolute(media['url'], base_domain)
    if 'media_thumbnail' in entry:
        for media in entry.media_thumbnail:
            if 'url' in media:
                return make_absolute(media['url'], base_domain)
    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if 'href' in enc and enc.get('type', '').startswith('image'):
                return make_absolute(enc['href'], base_domain)
            if 'url' in enc and enc.get('type', '').startswith('image'):
                return make_absolute(enc['url'], base_domain)
    summary = entry.get('summary', '') or entry.get('description', '')
    if summary:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.IGNORECASE)
        if match:
            return make_absolute(match.group(1), base_domain)
    content = entry.get('content', [])
    for c in content:
        if 'value' in c:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c['value'], re.IGNORECASE)
            if match:
                return make_absolute(match.group(1), base_domain)
    return None

def is_youtube_video(url):
    return ('youtube.com/watch' in url) or ('youtu.be/' in url)

def to_short_youtube_url(url):
    """
    Преобразует любую YouTube-ссылку в короткую youtu.be/ID,
    включая ссылки с закодированными символами (%3F, %3D).
    """
    # Декодируем URL, чтобы убрать %3F, %3D и т.п.
    decoded_url = unquote(url)
    # Извлекаем ID видео
    video_id = None
    if 'youtube.com/watch' in decoded_url:
        match = re.search(r'v=([^&]+)', decoded_url)
        if match:
            video_id = match.group(1)
    elif 'youtu.be/' in decoded_url:
        match = re.search(r'youtu\.be/([^?&]+)', decoded_url)
        if match:
            video_id = match.group(1)
    if video_id:
        return f"https://youtu.be/{video_id}"
    return decoded_url  # если не получилось, возвращаем декодированную ссылку

def extract_video_url_from_page(soup):
    if not soup:
        return None, False

    # 1. Прямые видеофайлы (mp4/webm) – приоритет
    video_tag = soup.select_one('video')
    if video_tag:
        src = video_tag.get('src')
        if src and re.search(r'\.(mp4|webm)(\?.*)?$', src, re.IGNORECASE):
            return src, False
        source_tag = video_tag.select_one('source')
        if source_tag and source_tag.get('src') and re.search(r'\.(mp4|webm)(\?.*)?$', source_tag['src'], re.IGNORECASE):
            return source_tag['src'], False

    og_video = soup.select_one('meta[property="og:video"]')
    if og_video and og_video.get('content'):
        url = og_video['content']
        if re.search(r'\.(mp4|webm)(\?.*)?$', url, re.IGNORECASE):
            return url, False

    scripts = soup.find_all('script')
    script_text = ' '.join(s.get_text() for s in scripts)
    pattern = r'(?:sources|vodQualities)[^{]*?["\']src["\']\s*:\s*["\']([^"\']+\.(?:mp4|webm))'
    match = re.search(pattern, script_text, re.IGNORECASE)
    if match:
        return html.unescape(match.group(1)), False

    # 2. YouTube видео
    yt_tag = soup.select_one('editor-body-youtube')
    if yt_tag and yt_tag.get('url'):
        url = yt_tag['url']
        if is_youtube_video(url):
            return url, True

    iframe = soup.select_one('iframe[src*="youtube.com/embed"], iframe[src*="youtu.be/"]')
    if iframe and iframe.get('src'):
        return iframe['src'], True

    if og_video and og_video.get('content'):
        url = og_video['content']
        if is_youtube_video(url):
            return url, True

    for a in soup.select('a.youtube'):
        href = a.get('href', '')
        match = re.search(r'url=([^&]+)', href)
        if match:
            url = html.unescape(match.group(1))
            if is_youtube_video(url):
                return url, True

    return None, False

def fetch_video_info(entry, soup=None):
    if soup is None:
        link = entry.get('link')
        if link:
            soup = get_page_soup(link)
    if soup:
        return extract_video_url_from_page(soup)
    return None, False

def download_youtube_video(youtube_url):
    """Скачивает YouTube-видео и возвращает BytesIO."""
    try:
        ydl_opts = {
            'format': 'best[ext=mp4]',
            'outtmpl': '-',
            'quiet': True,
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            video_url = info.get('url')
            if video_url:
                r = requests.get(video_url, stream=True, timeout=30)
                r.raise_for_status()
                video_bytes = io.BytesIO(r.content)
                video_bytes.seek(0)
                return video_bytes
    except Exception as e:
        print(f"Не удалось скачать YouTube-видео {youtube_url}: {e}")
    return None

def download_image(url, referer=None):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        if referer:
            headers['Referer'] = referer
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        return io.BytesIO(r.content)
    except Exception as e:
        print(f"Не удалось скачать изображение {url}: {e}")
        return None

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

    unwanted_phrases = [
        r'Читать дальше\s*→?',
        r'Читать полностью\s*:?',
        r'Источник\s*:',
    ]
    for pattern in unwanted_phrases:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

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

    return "\n\n".join(paragraphs)

def escape_html(text):
    return html.escape(text, quote=False)

def make_hashtag(text):
    words = text.strip().split()
    clean_words = []
    for w in words:
        clean_w = re.sub(r'[^\w]', '', w, flags=re.UNICODE)
        if clean_w:
            clean_words.append(clean_w.lower())
    if not clean_words:
        return None
    return '#' + '_'.join(clean_words)

def extract_title_hashtag(title):
    match = re.search(r'«([^»]+)»', title)
    if not match:
        match = re.search(r'"([^"]+)"', title)
    if match:
        anime_name = match.group(1).strip()
        return make_hashtag(anime_name)
    return None

def build_post_html(title, body, emoji='📄'):
    title_esc = escape_html(title)
    body_formatted = format_news_body(body) if body else ""

    parts = [f"{emoji} <b>{title_esc}</b>"]

    if body_formatted:
        parts.append("┄┄┄ ✦ ┄┄┄")
        parts.append(body_formatted)

    hashtags = ["#аниме", "#новости"]
    title_tag = extract_title_hashtag(title)
    if title_tag and title_tag not in hashtags:
        hashtags.append(title_tag)

    parts.append("")
    parts.append("🏷️ " + " ".join(hashtags))

    return "\n".join(parts)

def is_podcast_entry(entry):
    """Пропускаем выпуски подкастов (например, ЕВА-699)."""
    title = entry.get('title', '')
    link = entry.get('link', '')
    if re.match(r'^ЕВА-\d+', title, re.IGNORECASE):
        return True
    if 'ЕВА' in title.upper() and '/comments/' in link:
        return True
    if re.search(r'/eva\d+', link, re.IGNORECASE):
        return True
    return False

def send_post(title, body, link, image_url, video_url, is_youtube):
    if video_url and is_youtube:
        emoji = '🎬'
    elif video_url and not is_youtube:
        emoji = '🎞️'
    elif image_url:
        emoji = '🖼️'
    else:
        emoji = '📄'

    if video_url or image_url:
        body = smart_truncate(body, 800) if body else ""
    else:
        body = smart_truncate(body, 3000) if body else ""

    message_text = build_post_html(title, body, emoji)

    # Прямое видео (mp4/webm)
    if video_url and not is_youtube:
        try:
            bot.send_video(CHANNEL_ID, video_url, caption=message_text[:1024], parse_mode='HTML')
            return
        except Exception as e:
            print(f"Не удалось отправить видео: {e}")

    # YouTube: пробуем скачать и отправить как видео
    if video_url and is_youtube:
        video_file = download_youtube_video(video_url)
        if video_file:
            try:
                bot.send_video(CHANNEL_ID, video_file, caption=message_text[:1024], parse_mode='HTML')
                return
            except Exception as e:
                print(f"Не удалось отправить скачанное видео: {e}")

        # Если не удалось скачать, отправляем короткую ссылку с превью
        short_url = to_short_youtube_url(video_url)
        bot.send_message(
            CHANNEL_ID,
            message_text + f"\n\nСмотреть: {short_url}",
            parse_mode='HTML',
            disable_web_page_preview=False
        )
        return

    # Картинка
    if image_url:
        image_file = download_image(image_url, referer=link)
        if image_file:
            try:
                bot.send_photo(CHANNEL_ID, image_file, caption=message_text[:1024], parse_mode='HTML')
                return
            except Exception as e:
                print(f"Не удалось отправить фото: {e}")

    # Обычное сообщение
    bot.send_message(CHANNEL_ID, message_text, parse_mode='HTML', disable_web_page_preview=True)

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

        for entry in feed.entries[:10]:
            if is_podcast_entry(entry):
                print(f"Пропущен подкаст: {entry.get('title')}")
                continue

            link = entry.get('link', '')
            if not link or link in posted_links:
                continue

            title = entry.get('title', 'Без названия')

            soup = get_page_soup(link) if link else None
            full_text = extract_full_text_from_page(soup) if soup else fetch_full_text(entry)
            image_url = fetch_image_url(entry, soup)
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
