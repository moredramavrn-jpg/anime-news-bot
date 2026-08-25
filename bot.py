import os
import feedparser
from groq import Groq
import telebot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

RSS_URLS = [
    "https://www.animenewsnetwork.com/news/rss.xml",
    "https://www.crunchyroll.com/news/rss",
    "https://myanimelist.net/rss/news.xml"
]

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

def generate_post(title, summary):
    prompt = f"""Ты редактор аниме-канала. Напиши короткий пост для Telegram на основе новости.
Заголовок: {title}
Описание: {summary}
Пост должен содержать:
- Привлекательный заголовок (можешь слегка перефразировать)
- Краткое описание из 2-3 предложений
- 2-3 подходящих эмодзи
- Хэштеги #аниме #новости
Не выдумывай факты, используй только предоставленную информацию."""
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "Ты редактор аниме-канала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Ошибка генерации: {e}")
        return f"{title}\n\n{summary[:200]}...\n\n#аниме #новости"

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

        for entry in feed.entries[:5]:
            link = entry.get('link', '')
            if not link or link in posted_links:
                continue

            title = entry.get('title', 'Без названия')
            summary = entry.get('summary', '') or entry.get('description', '') or ''
            import re
            summary = re.sub(r'<[^>]+>', '', summary).strip()

            post_text = generate_post(title, summary)

            try:
                bot.send_message(CHANNEL_ID, post_text)
                posted_links.add(link)
                new_posts += 1
                print(f"Опубликовано: {title}")
            except Exception as e:
                print(f"Ошибка отправки для {link}: {e}")

    if new_posts > 0:
        save_posted(posted_links)
        print(f"Сохранено {new_posts} новых ссылок в posted.txt")
    else:
        print("Новых новостей нет.")

if __name__ == "__main__":
    main()
