import os
import random
import requests
import urllib3
import telebot
from io import BytesIO
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

PIKABU_SERIES_URLS = [
    "https://pikabu.ru/series/anime_memyi_59562",
    "https://pikabu.ru/series/podborki_randomnyikh_anime_memov_31394"
]

POSTED_IDS_FILE = "posted_memes.txt"
LAST_TYPE_FILE = "last_meme_type.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

def load_posted_ids():
    if not os.path.exists(POSTED_IDS_FILE):
        return set()
    with open(POSTED_IDS_FILE, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}

def save_posted_id(post_id):
    with open(POSTED_IDS_FILE, 'a', encoding='utf-8') as f:
        f.write(post_id + '\n')

def get_last_type():
    if os.path.exists(LAST_TYPE_FILE):
        with open(LAST_TYPE_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return None

def save_last_type(meme_type):
    with open(LAST_TYPE_FILE, 'w', encoding='utf-8') as f:
        f.write(meme_type)

def get_posts_from_series(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers, timeout=20)
    soup = BeautifulSoup(r.text, "lxml")

    posts = []
    for article in soup.select("article.story"):
        post_id = article.get("data-story-id", "")

        content = article.select_one(".story__content")
        if content:
            text_blocks = content.select(".story-block_type_text")
            if text_blocks:
                continue

        title_tag = article.select_one("a.story__title-link")
        title = title_tag.get_text(strip=True) if title_tag else "Без названия"
        link = title_tag.get("href") if title_tag else ""

        video_tag = article.select_one("video")
        if video_tag:
            video_url = None

            source_tag = video_tag.select_one("source")
            if source_tag:
                video_url = source_tag.get("src")

            if not video_url:
                data_source = video_tag.get("data-source")
                if data_source:
                    if data_source.startswith("//"):
                        data_source = "https:" + data_source
                    if not data_source.endswith(".mp4"):
                        video_url = data_source + ".mp4"
                    else:
                        video_url = data_source

            if video_url and video_url.startswith("//"):
                video_url = "https:" + video_url

            if video_url:
                posts.append({"id": post_id, "title": title, "link": link, "type": "video", "media_url": video_url})
                continue

        img_tag = article.select_one("img.story-image__image")
        if img_tag:
            img_url = (
                img_tag.get("data-large-image") or
                img_tag.get("data-src") or
                img_tag.get("src")
            )
            if img_url and img_url.startswith("//"):
                img_url = "https:" + img_url
            if img_url:
                posts.append({"id": post_id, "title": title, "link": link, "type": "image", "media_url": img_url})

    return posts

def download_media(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return BytesIO(r.content)
    except Exception as e:
        print(f"Ошибка скачивания: {e}")
        return None

def main():
    posted_ids = load_posted_ids()
    all_posts = []

    for url in PIKABU_SERIES_URLS:
        posts = get_posts_from_series(url)
        all_posts.extend(posts)

    all_posts = [p for p in all_posts if p["id"] not in posted_ids]
    if not all_posts:
        print("Нет новых мемов")
        return

    last_type = get_last_type()
    desired_type = "video" if last_type == "image" else "image"

    print(f"last_type = {last_type}, desired_type = {desired_type}")

    filtered = [p for p in all_posts if p["type"] == desired_type]
    if not filtered:
        print(f"Нет мемов типа {desired_type}, берём любой")
        filtered = all_posts  # если нет нужного типа, берём любой

    post = random.choice(filtered)
    print(f"Выбран пост: {post['title']} (тип: {post['type']})")

    media_bytes = download_media(post["media_url"])
    if not media_bytes:
        print("Не удалось скачать медиа")
        return

    if post["type"] == "video":
        bot.send_video(CHANNEL_ID, media_bytes, caption="#аниме #мем")
    else:
        caption = f"{post['title']}\n\n#аниме #мем"
        bot.send_photo(CHANNEL_ID, media_bytes, caption=caption)

    save_posted_id(post["id"])
    save_last_type(post["type"])
    print("Мем опубликован.")

if __name__ == "__main__":
    main()
