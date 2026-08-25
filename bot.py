import os
import re
import html
import io
import json
import base64
import uuid
import time
import urllib3
import feedparser
import telebot
import requests
import yt_dlp
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup
from telebot import types

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

RSS_URLS = [
    "https://www.goha.ru/rss/anime",
    "https://kg-portal.ru/rss/news_anime.rss"
]

POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

# ---------- Работа с опубликованными ----------
def normalize_title(title):
    return re.sub(r'[^\w]', '', title.lower())

def load_posted():
    links = set()
    titles = set()
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('LINK:'):
                    links.add(line[5:])
                elif line.startswith('TITLE:'):
                    titles.add(line[6:])
                elif line:
                    links.add(line)
    return links, titles

def save_posted(links, titles):
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in links:
            f.write(f"LINK:{link}\n")
        for title in titles:
            f.write(f"TITLE:{title}\n")

def is_duplicate(link, title, links, titles):
    if link and link in links:
        return True
    norm_title = normalize_title(title)
    if norm_title in titles:
        return True
    for existing_title in titles:
        if SequenceMatcher(None, norm_title, existing_title).ratio() > 0.9:
            return True
    return False

# ---------- HTML / парсинг ----------
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
            'div.detail__text', 'div.news-full__text', 'div.article-body'
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

# ---------- Изображения ----------
def extract_image_from_page(soup, page_url=None):
    if not soup:
        return None

    selectors = [
        'div.editor-body-image img', 'div.editor-body img',
        'div.news_cover_center img', 'div.news_text img',
        'div.news_box img', 'article img', 'div.news_image img',
        'div.article_image img', 'div.full_news img',
        'div.news_content img', 'div.news-full__text img',
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

# ---------- Видео ----------
def is_youtube_video(url):
    return ('youtube.com/watch' in url) or ('youtu.be/' in url)

def to_short_youtube_url(url):
    decoded_url = unquote(url)
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
    return decoded_url

def extract_video_url_from_page(soup):
    if not soup:
        return None, False

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

# ---------- Обработка текста ----------
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

def truncate_by_words(text, max_len):
    if len(text) <= max_len:
        return text
    words = text.split()
    result = []
    current_len = 0
    for w in words:
        if current_len + len(w) + 1 > max_len:
            break
        result.append(w)
        current_len += len(w) + 1
    return ' '.join(result)

def strip_html_tags(text):
    return re.sub(r'<[^>]+>', '', text)

def fix_quotes(text):
    # Замена прямых кавычек на ёлочки
    result = []
    open_quote = False
    for ch in text:
        if ch == '"':
            if not open_quote:
                result.append('«')
                open_quote = True
            else:
                result.append('»')
                open_quote = False
        else:
            result.append(ch)
    # Заменяем „...“ на «...»
    text = ''.join(result)
    text = text.replace('„', '«').replace('“', '»')
    return text

def fix_punctuation_spaces(text):
    # Убираем пробел перед знаками препинания
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    # Убираем пробел после открывающей кавычки
    text = re.sub(r'(«)\s+', r'\1', text)
    # Убираем пробел перед закрывающей кавычкой
    text = re.sub(r'\s+(»)', r'\1', text)
    return text

def remove_garbage_lines(text):
    """Удаляет строки, которые выглядят как рассуждения или вопросы."""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        # Если строка содержит риторический вопрос или начинается с сомнительных слов – пропускаем
        if re.search(r'\?\s*$', line):
            continue
        if re.match(r'^(Что за|Впрочем|Но и|Как думаете|Кстати|Наверное|Возможно)', line, re.IGNORECASE):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)

def clean_and_paragraph(text):
    if not text:
        return ""
    text = re.sub(r'\s*\n\s*', ' ', text)
    text = re.sub(r' {2,}', ' ', text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) <= 1:
        return text
    paragraphs = []
    current = []
    LONG_SENTENCE_THRESHOLD = 120
    for sent in sentences:
        if len(sent) >= LONG_SENTENCE_THRESHOLD:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            paragraphs.append(sent)
        else:
            current.append(sent)
            if len(current) == 2:
                paragraphs.append(" ".join(current))
                current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)

def format_news_body(text):
    text = clean_and_paragraph(text)
    text = fix_quotes(text)
    text = fix_punctuation_spaces(text)
    text = remove_garbage_lines(text)
    return text

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
    title = entry.get('title', '')
    link = entry.get('link', '')
    if re.match(r'^ЕВА-\d+', title, re.IGNORECASE):
        return True
    if 'ЕВА' in title.upper() and '/comments/' in link:
        return True
    if re.search(r'/eva\d+', link, re.IGNORECASE):
        return True
    return False

# ---------- GigaChat API ----------
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

def rewrite_news(title, body):
    if not GIGACHAT_AUTHORIZATION_KEY:
        print("GigaChat: нет ключа авторизации")
        return title, body

    token = get_gigachat_token()
    if not token:
        print("GigaChat: не удалось получить токен")
        return title, body

    body_part = body[:3000]

    prompt = f"""Перепиши следующие заголовок и текст новости так, чтобы они стали уникальными, но сохранили все ключевые факты, имена, названия.
Постарайся сохранить объём примерно 1500 символов. Не упускай важные детали.
Разбей текст на логические абзацы, каждый абзац должен содержать ровно 2 предложения.
Избегай дословного копирования. Используй стандартные кавычки «» и не ставь лишние пробелы.
Не задавай вопросов, не пиши комментарии от себя, не используй вводные слова-рассуждения.
Пиши на русском языке.

Заголовок: {title}

Текст: {body_part}

Выведи результат строго в формате:
Заголовок: <новый заголовок>
Текст: <новый текст>
"""
    try:
        response = requests.post(
            "https://api.giga.chat/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-ID": str(uuid.uuid4()),
                "User-Agent": "AnimeNewsBot/1.0"
            },
            json={
                "model": "GigaChat-3-Ultra",
                "messages": [
                    {"role": "system", "content": "Ты — редактор аниме-новостей."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1200
            },
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        generated_text = data["choices"][0]["message"]["content"].strip()

        new_title = title
        new_body = body
        for line in generated_text.split('\n'):
            line = line.strip()
            if line.startswith('Заголовок:'):
                new_title = line.replace('Заголовок:', '').strip()
            elif line.startswith('Текст:'):
                new_body = line.replace('Текст:', '').strip()

        # Финальная очистка
        new_title = fix_quotes(new_title)
        new_title = fix_punctuation_spaces(new_title)
        new_body = clean_and_paragraph(new_body)
        new_body = fix_quotes(new_body)
        new_body = fix_punctuation_spaces(new_body)
        new_body = remove_garbage_lines(new_body)

        if new_title and new_body:
            print(f"GigaChat вернул новый заголовок: {new_title[:50]}...")
            return new_title, new_body
        else:
            print("GigaChat: не удалось распознать результат")
            return title, body
    except Exception as e:
        print(f"Ошибка при рерайте через GigaChat: {e}")
        return title, body

def build_caption_fit(title, body, emoji, max_len=1024):
    full_html = build_post_html(title, body, emoji)
    plain_text = strip_html_tags(full_html)

    if len(plain_text) <= max_len:
        return full_html

    hashtags = ["#аниме", "#новости"]
    title_tag = extract_title_hashtag(title)
    if title_tag and title_tag not in hashtags:
        hashtags.append(title_tag)
    tags_str = " ".join(hashtags)

    title_plain = f"{emoji} {title}"
    separator_plain = "┄┄┄ ✦ ┄┄┄"
    footer_plain = f"🏷️ {tags_str}"

    base_len = len(title_plain) + len(separator_plain) + len(footer_plain) + 6
    available = max_len - base_len

    if available < 50:
        return truncate_by_words(plain_text, max_len)

    body_formatted = format_news_body(body)
    body_paragraphs = body_formatted.split('\n\n')
    chosen = []
    current_len = 0
    for para in body_paragraphs:
        para_plain = strip_html_tags(para)
        if current_len + len(para_plain) + 2 <= available:
            chosen.append(para)
            current_len += len(para_plain) + 2
        else:
            remaining = available - current_len
            if remaining > 20:
                truncated_para = truncate_by_words(para_plain, remaining)
                chosen.append(truncated_para)
            break

    truncated_body = '\n\n'.join(chosen)
    return f"{emoji} <b>{escape_html(title)}</b>\n{separator_plain}\n{truncated_body}\n\n🏷️ {tags_str}"

def send_post(title, body, link, image_url, video_url, is_youtube):
    if video_url and is_youtube:
        emoji = '🎬'
    elif video_url and not is_youtube:
        emoji = '🎞️'
    elif image_url:
        emoji = '🖼️'
    else:
        emoji = '📄'

    title, body = rewrite_news(title, body)

    if video_url or image_url:
        full_message = build_caption_fit(title, body, emoji, 1024)
    else:
        full_message = build_post_html(title, body, emoji)

    if video_url and not is_youtube:
        try:
            bot.send_video(CHANNEL_ID, video_url, caption=full_message[:1024], parse_mode='HTML')
            return
        except Exception as e:
            print(f"Не удалось отправить видео: {e}")

    if video_url and is_youtube:
        video_file = download_youtube_video(video_url)
        if video_file:
            try:
                bot.send_video(CHANNEL_ID, video_file, caption=full_message[:1024], parse_mode='HTML')
                return
            except Exception as e:
                print(f"Не удалось отправить скачанное видео: {e}")

        short_url = to_short_youtube_url(video_url)
        bot.send_message(
            CHANNEL_ID,
            full_message + f"\n\nСмотреть: {short_url}",
            parse_mode='HTML',
            disable_web_page_preview=False
        )
        return

    if image_url:
        image_file = download_image(image_url, referer=link)
        if image_file:
            try:
                bot.send_photo(CHANNEL_ID, image_file, caption=full_message[:1024], parse_mode='HTML')
                return
            except Exception as e:
                print(f"Не удалось отправить фото: {e}")

    bot.send_message(CHANNEL_ID, full_message, parse_mode='HTML', disable_web_page_preview=True)

def main():
    links, titles = load_posted()
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
            title = entry.get('title', 'Без названия')
            if is_duplicate(link, title, links, titles):
                print(f"Дубликат пропущен: {title}")
                continue

            soup = get_page_soup(link) if link else None
            full_text = extract_full_text_from_page(soup) if soup else fetch_full_text(entry)
            image_url = fetch_image_url(entry, soup)
            video_url, is_youtube = fetch_video_info(entry, soup)

            try:
                send_post(title, full_text, link, image_url, video_url, is_youtube)
                links.add(link)
                titles.add(normalize_title(title))
                new_posts += 1
                print(f"Опубликовано: {title}")
            except Exception as e:
                print(f"Ошибка отправки для {link}: {e}")

    if new_posts > 0:
        save_posted(links, titles)
        print(f"Сохранено {new_posts} новых записей в {POSTED_FILE}")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
