import os
import re
import feedparser
from groq import Groq
import telebot
import requests
from bs4 import BeautifulSoup

# ===== НАСТРОЙКИ =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ===== ИСТОЧНИКИ (RSS + Канобу) =====
RSS_URLS = [
    ]

KANOBU_URL = "https://kanobu.ru/anime/"

POSTED_FILE = "posted.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

def load_posted():
    if not os.path.exists(POSTED_FILE):
        return set()
    with open(POSTED_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_posted(posted_links):
    with open(POSTED_FILE, 'w', encoding='utf-8') as f:
        for link in posted_links:
            f.write(link + '\n')

def clean_thinking(text):
    if '<think>' in text:
        end_idx = text.find('</think>')
        if end_idx != -1:
            return text[end_idx + len('</think>'):].strip()
        else:
            start_idx = text.find('<think>')
            if start_idx != -1:
                return text[start_idx + len('<think>'):].strip()
    return text.strip()

def generate_post(title, summary):
    prompt = f"""Ты — редактор популярного аниме-канала в Telegram. Тебе дали новость (на русском или английском).
Твоя задача — сделать из неё уникальный пост, который не выглядит как копипаст.

Правила:
1. Перескажи своими словами, сохраняя ключевые факты.
2. Пиши на русском языке, от первого лица.
3. Добавь 2-3 эмодзи и хэштеги #аниме #новости.
4. Длина поста: 3-5 предложений.
5. Выведи ТОЛЬКО пост, без пояснений.

Исходная новость:
Заголовок: {title}
Описание: {summary}

Пост:"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",   # или qwen/qwen3.6-27b
            messages=[
                {"role": "system", "content": "Ты — талантливый копирайтер аниме-канала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
            max_tokens=400
        )
        raw = response.choices[0].message.content.strip()
        cleaned = clean_thinking(raw)
        return cleaned if cleaned else f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"
    except Exception as e:
        print(f"Ошибка генерации: {e}")
        return f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"

def extract_image_url(entry):
    # ... (оставьте как в предыдущем коде)
    # Для краткости здесь опущено, но вы можете вставить свою реализацию
    pass

# ===== ПАРСЕР КАНОБУ =====
def fetch_kanobu_news():
    """
    Загружает страницу Канобу и возвращает список новостей в формате:
    [{ 'title': str, 'link': str, 'summary': str, 'image_url': str }]
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(KANOBU_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')

        news_items = []

        # Ищем блоки новостей. В HTML есть секции с тегами <article> внутри <a href="/news/...">
        # Берём все ссылки на /news/ и рядом с ними заголовки и картинки
        for a in soup.select('a[href^="/news/"]'):
            href = a.get('href')
            if not href:
                continue
            full_link = "https://kanobu.ru" + href

            # Ищем заголовок внутри этой ссылки или рядом
            title_tag = a.select_one('p')
            if not title_tag:
                title_tag = a.find_parent('article').select_one('p') if a.find_parent('article') else None
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title:
                continue

            # Ищем картинку поблизости
            img_tag = a.select_one('img')
            if not img_tag and a.find_parent('article'):
                img_tag = a.find_parent('article').select_one('img')
            image_url = img_tag.get('src') if img_tag else None

            # Описание чаще всего отсутствует на главной, поэтому оставим пустым
            summary = ""

            news_items.append({
                'title': title,
                'link': full_link,
                'summary': summary,
                'image_url': image_url
            })

        # Убираем дубли по ссылке
        unique = []
        seen = set()
        for item in news_items:
            if item['link'] not in seen:
                seen.add(item['link'])
                unique.append(item)
        return unique[:10]  # берём до 10 новостей

    except Exception as e:
        print(f"Ошибка парсинга Канобу: {e}")
        return []

# ===== ОСНОВНАЯ ЛОГИКА =====
def main():
    posted_links = load_posted()
    new_posts = 0

    # 1. Обработка RSS
    for rss_url in RSS_URLS:
        print(f"Обрабатываю ленту: {rss_url}")
        try:
            feed = feedparser.parse(rss_url)
        except Exception as e:
            print(f"Не удалось получить ленту {rss_url}: {e}")
            continue

        for entry in feed.entries[:5]:
            link = entry.get('link', '')
            if not link or link in posted_links:
                continue

            title = entry.get('title', 'Без названия')
            summary = entry.get('summary', '') or entry.get('description', '') or ''
            clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
            image_url = extract_image_url(entry)

            post_text = generate_post(title, clean_summary)

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
                print(f"Опубликовано (RSS): {title}")
            except Exception as e:
                print(f"Ошибка отправки для {link}: {e}")

    # 2. Обработка Канобу
    kanobu_news = fetch_kanobu_news()
    for item in kanobu_news:
        link = item['link']
        if link in posted_links:
            continue

        title = item['title']
        summary = item['summary']  # пусто, но можно передать заголовок
        image_url = item['image_url']

        # Генерируем пост (если summary пустое, передаём заголовок)
        post_text = generate_post(title, summary or title)

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
            print(f"Опубликовано (Канобу): {title}")
        except Exception as e:
            print(f"Ошибка отправки для {link}: {e}")

    if new_posts > 0:
        save_posted(posted_links)
        print(f"Сохранено {new_posts} новых ссылок в {POSTED_FILE}")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
