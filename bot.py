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

SHIKIMORI_MAIN = "https://shikimori.io/"

POSTED_FILE = "posted.txt"
RECENT_TITLES_FILE = "recent_titles.json"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

# ---------- Утилита: длина текста как её считает Telegram ----------
def telegram_len(s):
    """Telegram считает длину текста в UTF-16 code units, а не в Python len()."""
    if not s:
        return 0
    return len(s.encode('utf-16-le')) // 2

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

# ---------- Хранение недавних заголовков ----------
def load_recent_titles():
    if os.path.exists(RECENT_TITLES_FILE):
        with open(RECENT_TITLES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_recent_titles(titles):
    cutoff = time.time() - 7 * 86400
    titles = [t for t in titles if t.get("timestamp", 0) > cutoff]
    with open(RECENT_TITLES_FILE, 'w', encoding='utf-8') as f:
        json.dump(titles, f, ensure_ascii=False)

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

def giga_request(prompt, max_tokens=500):
    token = get_gigachat_token()
    if not token:
        return ""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-ID": str(uuid.uuid4()),
        "User-Agent": "AnimeNewsBot/1.0"
    }
    payload = {
        "model": "GigaChat-3-Ultra",
        "messages": [
            {"role": "system", "content": "Ты — помощник для проверки новостей."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens
    }
    try:
        r = requests.post("https://api.giga.chat/v1/chat/completions",
                          headers=headers, json=payload, timeout=30, verify=False)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Ошибка GigaChat: {e}")
        return ""

def is_similar_news(title, body, recent_titles):
    if not recent_titles:
        return False

    recent = '\n'.join([t["title"] for t in recent_titles[-20:]])
    prompt = f"""Сравни новую новость с уже опубликованными за последние 7 дней.

Новая новость:
Заголовок: {title}
Текст: {body[:500]}

Уже опубликованные:
{recent}

Ответь только "да", если новая новость по сути такая же (дубликат), иначе "нет"."""
    answer = giga_request(prompt, max_tokens=10)
    return answer.strip().lower() == "да"

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

def extract_russian_anime_names(soup):
    pairs = {}
    if not soup:
        return pairs

    for name_en in soup.select('span.name-en'):
        parent = name_en.find_parent()
        if parent:
            name_ru = parent.select_one('span.name-ru')
            if name_ru:
                en = name_en.get_text(strip=True)
                ru = name_ru.get_text(strip=True)
                if en and ru:
                    pairs[en] = ru
    return pairs

def replace_anime_names(text, name_pairs):
    if not text or not name_pairs:
        return text
    for en, ru in name_pairs.items():
        text = text.replace(en, ru)
        text = text.replace(f'«{en}»', f'«{ru}»')
        text = text.replace(f'"{en}"', f'«{ru}»')
    return text

def extract_full_text_from_page(soup):
    if not soup:
        return ""

    for bad_selector in [
        'div.b-comments', 'div.comments', 'div.b-comment', 'div.comment',
        'div.b-toolbar', 'div.toolbar', 'footer', 'header',
        'div.b-comments__content', 'div.comments__content'
    ]:
        for elem in soup.select(bad_selector):
            elem.decompose()

    main_content = soup.select_one('div.editor-body')  # Goha.ru
    if not main_content:
        main_content = soup.select_one('div.news_text')  # КГ-Портал

    if not main_content:
        main_content = soup.select_one('div.body-inner')  # Shikimori

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

def collapse_repeated_phrases(text, max_words=8):
    """
    Убирает подряд идущие повторы одной и той же фразы длиной 1-8 слов.
    Также удаляет дублирование, когда одно и то же слово повторяется подряд.
    """
    if not text:
        return text
    
    # Сначала убираем простое дублирование слов подряд
    # Например: "Наруто Наруто" -> "Наруто"
    words = text.split()
    result = []
    i = 0
    while i < len(words):
        # Проверяем дублирование одного слова
        if i + 1 < len(words) and words[i].lower() == words[i + 1].lower():
            # Убираем дублирование специальных слов (имена, названия)
            # Проверяем, что это не часть нормальной фразы (например, "то то")
            if words[i].lower() not in ['то', 'на', 'по', 'за', 'из', 'от']:
                result.append(words[i])
                i += 2
                continue
        result.append(words[i])
        i += 1
    
    # Теперь проверяем дублирование фраз (2-8 слов)
    text = ' '.join(result)
    words = text.split()
    result = []
    i = 0
    n_words = len(words)
    
    while i < n_words:
        matched = False
        # Пробуем найти повтор фразы
        for n in range(min(max_words, (n_words - i) // 2), 1, -1):
            first = [w.lower().strip('«»"\'.,;:!?') for w in words[i:i + n]]
            second = [w.lower().strip('«»"\'.,;:!?') for w in words[i + n:i + 2 * n]]
            # Проверяем совпадение (игнорируем регистр и знаки препинания)
            if first == second and any(first) and len(' '.join(first)) > 2:
                result.extend(words[i:i + n])
                i += 2 * n
                matched = True
                break
        if not matched:
            result.append(words[i])
            i += 1
    
    return ' '.join(result)

def clean_shikimori_links(text):
    """
    Очищает текст от дублей, создаваемых ссылками на Shikimori.
    Например: «NarutoНаруто» -> «Наруто»
    Hayato DateХаято Датэ -> Хаято Датэ
    """
    if not text:
        return text
    
    # Убираем дубли в кавычках: «NarutoНаруто» -> «Наруто»
    # Ищем паттерн: «[англ. название][рус. название]» и оставляем только русское
    pattern = r'«([A-Za-z0-9\s]+)([А-Яа-я\s]+)»'
    text = re.sub(pattern, r'«\2»', text)
    
    # Тоже самое без кавычек: NarutoНаруто -> Наруто
    # Проверяем, что вторая часть — это русское название
    def replace_en_ru(match):
        en_part = match.group(1).strip()
        ru_part = match.group(2).strip()
        # Если русская часть больше 2 символов и это похоже на имя/название
        if len(ru_part) > 2 and re.search(r'[А-Я]', ru_part):
            return ru_part
        return match.group(0)
    
    # Сначала ищем английское слово за которым сразу идёт русское
    # Например: "Hayato DateХаято Датэ" -> "Хаято Датэ"
    pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)([А-Я][а-я]+(?:\s+[А-Я][а-я]+)*)'
    text = re.sub(pattern, replace_en_ru, text)
    
    # Убираем дубли имён: "Хаято Датэ Хаято Датэ" -> "Хаято Датэ"
    pattern = r'([А-Я][а-я]+\s+[А-Я][а-я]+)\s+\1'
    text = re.sub(pattern, r'\1', text)
    
    # Убираем дубли названий: "Наруто Наруто" -> "Наруто"
    pattern = r'([А-Я][а-я]+)\s+\1(?=\s|$|[,.:;!?])'
    text = re.sub(pattern, r'\1', text)
    
    return text

def clean_duplicate_title(title):
    """Убирает дублирование в заголовке"""
    if not title:
        return title
    
    # Убираем дубли: «NarutoНаруто» -> «Наруто»
    title = re.sub(r'«([A-Za-z0-9\s]+)([А-Яа-я\s]+)»', r'«\2»', title)
    
    # Убираем дубли имён в заголовке
    title = re.sub(r'([А-Я][а-я]+\s+[А-Я][а-я]+)\s+\1', r'\1', title)
    title = re.sub(r'([А-Я][а-я]+)\s+\1(?=\s|$|[,.:;!?])', r'\1', title)
    
    # Убираем повтор одинаковых слов подряд
    words = title.split()
    result = []
    for w in words:
        if result and w.lower() == result[-1].lower():
            continue
        result.append(w)
    
    return ' '.join(result)

def fetch_full_text(entry):
    link = entry.get('link', '')

    if 'shikimori' in link:
        soup = get_page_soup(link)
        if soup:
            body_inner = soup.select_one('div.body-inner')
            if body_inner:
                full_text = clean_html(str(body_inner))
                if full_text:
                    full_text = collapse_repeated_phrases(full_text)
                    full_text = clean_shikimori_links(full_text)
                    full_text = re.sub(r'^([^.!?]+)\s+\1\s*', r'\1 ', full_text, flags=re.IGNORECASE)
                    return full_text
        summary = entry.get('summary', '') or entry.get('description', '')
        if summary:
            return clean_html(summary)
        return ""

    if link:
        soup = get_page_soup(link)
        if soup:
            full_text = extract_full_text_from_page(soup)
            if full_text:
                full_text = collapse_repeated_phrases(full_text)
                full_text = clean_shikimori_links(full_text)
                return full_text
    summary = entry.get('summary', '') or entry.get('description', '')
    if summary:
        return clean_html(summary)
    return ""

def extract_image_from_page(soup, page_url=None):
    if not soup:
        return None

    selectors = [
        'div.editor-body-image img', 'div.editor-body img',
        'div.news_cover_center img', 'div.news_text img',
        'div.news_box img', 'article img', 'div.news_image img',
        'div.article_image img', 'div.full_news img',
        'div.news_content img', 'div.news-full__text img',
        'div.b-shiki_editor img', 'div.shiki_editor img',
        'div.b-shiki_wall img'
    ]

    for selector in selectors:
        img_tag = soup.select_one(selector)
        if img_tag:
            src = (img_tag.get('src') or img_tag.get('data-src') or
                   img_tag.get('data-original') or img_tag.get('data-lazy-src'))
            if src:
                return make_absolute(src, page_url or 'https://shikimori.one')

    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get('content'):
        return make_absolute(og_image['content'], page_url or 'https://shikimori.one')

    for img in soup.find_all('img'):
        src = (img.get('src') or img.get('data-src') or
               img.get('data-original') or img.get('data-lazy-src'))
        if src and re.search(r'\.(jpg|jpeg|png|webp)(\?.*)?$', src, re.IGNORECASE):
            return make_absolute(src, page_url or 'https://shikimori.one')

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
        elif 'shikimori.one' in link:
            base_domain = 'https://shikimori.one'

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
    return (
        'youtube.com/watch' in url or
        'youtu.be/' in url or
        'youtube.com/embed/' in url or
        'youtube-nocookie.com/embed/' in url
    )

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
    elif 'youtube.com/embed/' in decoded_url:
        match = re.search(r'youtube\.com/embed/([^?&]+)', decoded_url)
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

    for a in soup.select('div.b-video.youtube a.video-link, a.video-link[data-href*="youtube"], a.video-link[href*="youtube"]'):
        data_href = a.get('data-href') or a.get('href')
        if data_href:
            url = html.unescape(data_href)
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
    if telegram_len(text) <= max_len:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = ""
    for s in sentences:
        if telegram_len(result) + telegram_len(s) + 1 > max_len:
            break
        result = (result + " " + s).strip()
    if not result:
        return text[:max_len]
    return result

def truncate_by_words(text, max_len):
    if telegram_len(text) <= max_len:
        return text
    words = text.split()
    result = []
    current_len = 0
    for w in words:
        w_len = telegram_len(w) + 1
        if current_len + w_len > max_len:
            break
        result.append(w)
        current_len += w_len
    return ' '.join(result)

def truncate_to_full_sentences(text, max_len):
    if telegram_len(text) <= max_len:
        return text
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []
    current_len = 0
    for s in sentences:
        sep_len = 1 if result else 0
        s_len = telegram_len(s) + sep_len
        if current_len + s_len <= max_len:
            result.append(s)
            current_len += s_len
        else:
            break
    return ' '.join(result)

def strip_html_tags(text):
    return re.sub(r'<[^>]+>', '', text)

def normalize_whitespace(text):
    if not text:
        return text
    return re.sub(r'\s+', ' ', text).strip()

def fix_quotes(text):
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
    text = ''.join(result)
    text = text.replace('„', '«').replace('“', '»')
    return text

def fix_punctuation_spaces(text):
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    text = re.sub(r'(«)\s+', r'\1', text)
    text = re.sub(r'\s+(»)', r'\1', text)
    return text

def remove_garbage_lines(text):
    lines = text.split('\n')
    cleaned = []
    for line in lines:
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
    title_esc = escape_html(normalize_whitespace(title))
    body_formatted = format_news_body(body) if body else ""

    parts = [f"{emoji} <b>{title_esc}</b>"]

    if body_formatted:
        parts.append("┄┄┄ ✦ ┄┄┄")
        parts.append(body_formatted)

    hashtags = ["#аниме", "#новости"]

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

def remove_duplicate_start(title, body):
    if not title or not body:
        return body
    title_clean = re.sub(r'<[^>]+>', '', title).strip().strip('«»').strip()
    body_clean = re.sub(r'<[^>]+>', '', body).strip()
    if body_clean.lower().startswith(title_clean.lower()):
        body_clean = body_clean[len(title_clean):].lstrip('.,;:!? ')
        if not body_clean:
            return ""
    sentences = re.split(r'(?<=[.!?])\s+', body_clean)
    if sentences and title_clean.lower() in sentences[0].lower():
        body_clean = ' '.join(sentences[1:]).strip()
    return body_clean

def rewrite_news(title, body, target_len=None):
    if not GIGACHAT_AUTHORIZATION_KEY:
        return title, body

    token = get_gigachat_token()
    if not token:
        return title, body

    body_part = body[:4000]
    source_len = telegram_len(body_part)

    if target_len is None:
        target_len = source_len

    needs_compression = source_len > target_len * 1.1

    if needs_compression:
        length_instruction = f"""ЦЕЛЕВАЯ ДЛИНА: примерно {target_len} символов (можно на 10-15% меньше, но не больше).
Оригинал длиннее цели, поэтому нужно СОКРАТИТЬ текст — но не механической обрезкой, а умным пересказом:
убери второстепенные детали и подробности, оставь только главную суть, ключевые факты, даты, имена и названия.
Текст ОБЯЗАТЕЛЬНО должен быть завершённым: заканчиваться полным предложением с точкой, доводить мысль до конца.
Никогда не обрывай текст на середине предложения или мысли — лучше выбрось менее важную деталь целиком,
чем оставить незаконченную фразу."""
    else:
        length_instruction = f"""ЦЕЛЕВАЯ ДЛИНА: примерно {target_len} символов, плюс-минус немного.
Не сокращай текст искусственно и не выбрасывай детали без необходимости — просто перескажи своими словами
примерно того же объёма. Текст должен заканчиваться полным, законченным предложением."""

    prompt = f"""Ты — опытный журналист новостного портала об аниме. Перепиши текст новости своими словами,
как будто пишешь для своей редакции — живо, естественно, без канцелярита и без ощущения, что текст писала нейросеть.

{length_instruction}

СТИЛЬ — ПИШИ КАК ЖИВОЙ ЧЕЛОВЕК:
- Пиши так, как обычный человек рассказывает интересную новость другу: простыми, естественными фразами.
- Варьируй начала предложений и абзацев — не начинай два абзаца подряд одинаковой конструкцией.
- Используй разную длину предложений: где-то короткое и хлёсткое, где-то развёрнутое.
- Сохраняй все факты, имена, названия, даты и цифры ТОЧНО как в оригинале — здесь нельзя ошибаться.
- Используй кавычки «» для названий и цитат.
- Разбивай текст на абзацы по 2 предложения (если это не нарушает смысл).

ЖЁСТКО ЗАПРЕЩЕНО — так пишут нейросети, а не журналисты, никогда не используй:
- Канцелярские и вводные штампы: «стоит отметить», «важно отметить», «следует сказать», «таким образом»,
  «в заключение», «необходимо подчеркнуть», «нельзя не отметить», «отдельно стоит сказать».
- Слова-паразиты нейросетей: «безусловно», «несомненно», «в целом», «в общем и целом», «более того»,
  «примечательно, что», «интересно, что», «стоит также упомянуть».
- Однотипные вводные конструкции в начале двух и более абзацев подряд (например, два абзаца, начинающихся с «Также»).
- Риторические вопросы, обращения к читателю («как думаете?», «согласны?»).
- Собственное мнение, оценки, домыслы, предположения, которых нет в оригинале.
- Штампованные метафоры и клише («настоящий подарок для фанатов», «не оставит равнодушным»).

Заголовок должен быть конкретным и по существу, без кликбейта и без придуманных деталей.

Заголовок: {title}

Текст: {body_part}

Выведи результат СТРОГО в формате, без пояснений от себя:
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
                    {"role": "system", "content": "Ты — опытный редактор аниме-новостей, который пишет живым человеческим языком, а не канцеляритом. Ты всегда укладываешься в заданную длину текста и всегда доводишь мысль до конца, не обрывая текст на полуслове."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.6,
                "max_tokens": min(4000, max(800, target_len + 400))
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

        new_title = fix_quotes(new_title)
        new_title = fix_punctuation_spaces(new_title)
        new_body = clean_and_paragraph(new_body)
        new_body = fix_quotes(new_body)
        new_body = fix_punctuation_spaces(new_body)
        new_body = remove_garbage_lines(new_body)

        new_body = remove_duplicate_start(new_title, new_body)

        if new_title and new_body:
            new_len = telegram_len(new_body)
            print(f"GigaChat вернул новый заголовок: {new_title[:50]}...")
            print(f"[DEBUG] Длина после рерайта: {new_len}, цель: {target_len}, исходник: {source_len}")
            if new_len > target_len * 1.25 or new_len < target_len * 0.5:
                print(f"[WARNING] Длина рерайта сильно отклоняется от цели, используем оригинальный текст")
                return title, body
            return new_title, new_body
        else:
            return title, body
    except Exception as e:
        print(f"Ошибка при рерайте через GigaChat: {e}")
        return title, body

def build_caption_fit(title, body, emoji, max_len=1024):
    title = normalize_whitespace(title)
    full_html = build_post_html(title, body, emoji)
    plain_text = strip_html_tags(full_html)

    if telegram_len(plain_text) <= max_len:
        return full_html

    hashtags = ["#аниме", "#новости"]
    tags_str = " ".join(hashtags)

    title_plain = f"{emoji} {title}"
    separator_plain = "┄┄┄ ✦ ┄┄┄"
    footer_plain = f"🏷️ {tags_str}"

    base_len = (telegram_len(title_plain) + telegram_len(separator_plain) +
                telegram_len(footer_plain) + 4)
    available = max_len - base_len

    if available < 50:
        return f"{emoji} <b>{escape_html(title)}</b>\n\n🏷️ {tags_str}"

    body_formatted = format_news_body(body)
    body_paragraphs = body_formatted.split('\n\n')
    chosen = []
    current_len = 0
    for para in body_paragraphs:
        para_plain = strip_html_tags(para)
        sep_len = 2 if chosen else 0
        para_len = telegram_len(para_plain)
        if current_len + para_len + sep_len <= available:
            chosen.append(para)
            current_len += para_len + sep_len
        else:
            remaining = available - current_len - sep_len
            if remaining > 20:
                truncated_para = truncate_to_full_sentences(para_plain, remaining)
                if truncated_para:
                    chosen.append(truncated_para)
            break

    truncated_body = '\n\n'.join(chosen)
    return f"{emoji} <b>{escape_html(title)}</b>\n{separator_plain}\n{truncated_body}\n\n🏷️ {tags_str}"

def send_post(title, body, link, image_url, video_url, is_youtube):
    # Чистим заголовок от дублей
    title = clean_duplicate_title(title)
    body = clean_shikimori_links(body)
    
    if video_url and is_youtube:
        emoji = '🎬'
    elif video_url and not is_youtube:
        emoji = '🎞️'
    elif image_url:
        emoji = '🖼️'
    else:
        emoji = '📄'

    has_media = bool(image_url) or bool(video_url and not is_youtube)
    max_len_for_post = 1024 if has_media else 4096
    shell_reserve = telegram_len(title) + 90
    target_len = max(150, max_len_for_post - shell_reserve)

    print(f"[DEBUG] Текст ДО рерайта: {telegram_len(body)} символов, целевая длина: {target_len}")
    title, body = rewrite_news(title, body, target_len=target_len)
    print(f"[DEBUG] Текст ПОСЛЕ рерайта: {telegram_len(body)} символов")

    full_message_long = build_post_html(title, body, emoji)
    caption_message = None
    def get_caption():
        nonlocal caption_message
        if caption_message is None:
            caption_message = build_caption_fit(title, body, emoji, max_len=1024)
            print(f"[DEBUG] Итоговая подпись (caption): {telegram_len(caption_message)} символов")
        return caption_message

    if video_url and not is_youtube:
        try:
            bot.send_video(CHANNEL_ID, video_url, caption=get_caption(), parse_mode='HTML')
            return
        except Exception as e:
            print(f"Не удалось отправить видео: {e}")
            bot.send_message(CHANNEL_ID, full_message_long[:4096], parse_mode='HTML',
                              disable_web_page_preview=True)
            return

    if video_url and is_youtube:
        short_url = to_short_youtube_url(video_url)
        message_with_link = f"{full_message_long}\n\nСмотреть: {short_url}"
        bot.send_message(
            CHANNEL_ID,
            message_with_link[:4096],
            parse_mode='HTML',
            disable_web_page_preview=False
        )
        return

    if image_url:
        image_file = download_image(image_url, referer=link)
        if image_file:
            try:
                bot.send_photo(CHANNEL_ID, image_file, caption=get_caption(), parse_mode='HTML')
                return
            except Exception as e:
                print(f"Не удалось отправить фото: {e}")

    bot.send_message(CHANNEL_ID, full_message_long[:4096], parse_mode='HTML', disable_web_page_preview=True)

# ---------- ФУНКЦИЯ ДЛЯ ПАРСИНГА SHIKIMORI ----------
def fetch_shikimori_news_from_main_page():
    soup = get_page_soup(SHIKIMORI_MAIN)
    if not soup:
        return []

    news_items = []
    seen_links = set()

    latest = soup.select_one('div.news_wall.latest-news')
    if latest:
        for article in latest.select('article.b-news_wall-topic'):
            link = article.select_one('a[href*="/forum/news/"]')
            if link:
                href = urljoin(SHIKIMORI_MAIN, link.get('href', ''))
                title = article.select_one('div.title')
                title_text = title.get_text(strip=True) if title else "Без названия"
                img_tag = article.select_one('img')
                image_url = None
                if img_tag:
                    src = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-original')
                    if src:
                        image_url = make_absolute(src, SHIKIMORI_MAIN)
                if href not in seen_links:
                    news_items.append({'title': title_text, 'link': href, 'image_url': image_url})
                    seen_links.add(href)

    other = soup.select_one('div.news_wall.other-news')
    if other:
        for article in other.select('article.b-news_wall-topic'):
            link = article.select_one('a[href*="/forum/news/"]')
            if link:
                href = urljoin(SHIKIMORI_MAIN, link.get('href', ''))
                title = article.select_one('div.title')
                title_text = title.get_text(strip=True) if title else "Без названия"
                img_tag = article.select_one('img')
                image_url = None
                if img_tag:
                    src = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-original')
                    if src:
                        image_url = make_absolute(src, SHIKIMORI_MAIN)
                if href not in seen_links:
                    news_items.append({'title': title_text, 'link': href, 'image_url': image_url})
                    seen_links.add(href)

    return news_items

# ---------- ОСНОВНАЯ ФУНКЦИЯ ----------
def main():
    links, titles = load_posted()
    recent_titles = load_recent_titles()
    new_posts = 0

    print("Обрабатываю новости Shikimori с главной страницы...")
    shikimori_news = fetch_shikimori_news_from_main_page()
    for news in shikimori_news:
        link = news['link']
        title = news['title']
        if is_duplicate(link, title, links, titles):
            print(f"Дубликат пропущен: {title}")
            continue

        soup = get_page_soup(link)
        full_text = fetch_full_text({'link': link, 'title': title})

        if full_text:
            full_text = collapse_repeated_phrases(full_text)
            full_text = clean_shikimori_links(full_text)
            sentences = re.split(r'(?<=[.!?])\s+', full_text.strip())
            if sentences:
                title = normalize_whitespace(sentences[0])
                full_text = ' '.join(sentences[1:])
            full_text = remove_duplicate_start(title, full_text)

        if is_similar_news(title, full_text, recent_titles):
            print(f"Похожая новость пропущена: {title}")
            continue

        image_url = news.get('image_url')
        video_url, is_youtube = fetch_video_info({'link': link}, soup)

        name_pairs = extract_russian_anime_names(soup)
        if name_pairs:
            title = replace_anime_names(title, name_pairs)
            full_text = replace_anime_names(full_text, name_pairs)

        try:
            send_post(title, full_text, link, image_url, video_url, is_youtube)
            links.add(link)
            titles.add(normalize_title(title))
            recent_titles.append({"title": title, "timestamp": time.time()})
            new_posts += 1
            print(f"Опубликовано: {title}")
        except Exception as e:
            print(f"Ошибка отправки для {link}: {e}")

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
            full_text = fetch_full_text(entry)

            if is_similar_news(title, full_text, recent_titles):
                print(f"Похожая новость пропущена: {title}")
                continue

            image_url = fetch_image_url(entry, soup)
            video_url, is_youtube = fetch_video_info(entry, soup)

            try:
                send_post(title, full_text, link, image_url, video_url, is_youtube)
                links.add(link)
                titles.add(normalize_title(title))
                recent_titles.append({"title": title, "timestamp": time.time()})
                new_posts += 1
                print(f"Опубликовано: {title}")
            except Exception as e:
                print(f"Ошибка отправки для {link}: {e}")

    if new_posts > 0:
        save_posted(links, titles)
        save_recent_titles(recent_titles)
        print(f"Сохранено {new_posts} новых записей в {POSTED_FILE}")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
