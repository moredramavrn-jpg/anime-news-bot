import os
import re
import uuid
import time
import random
import urllib3
import telebot
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

TOP_ANIME_FILE = "top_anime.txt"
USED_ANIME_FILE = "used_anime.txt"

ITEM_EMOJI = ["🌸", "⚡", "🔥", "💥", "🌟"]

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

def load_top_anime():
    if not os.path.exists(TOP_ANIME_FILE):
        print(f"Файл {TOP_ANIME_FILE} не найден")
        return []

    anime_list = []
    with open(TOP_ANIME_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                anime_list.append(line)
    return anime_list

def load_used_anime():
    if not os.path.exists(USED_ANIME_FILE):
        return set()
    with open(USED_ANIME_FILE, 'r', encoding='utf-8') as f:
        return {line.strip().lower() for line in f if line.strip()}

def save_used_anime(anime_set):
    anime_list = list(anime_set)
    with open(USED_ANIME_FILE, 'w', encoding='utf-8') as f:
        for name in anime_list:
            f.write(name + '\n')

def giga_request(prompt, token, max_tokens=300):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-ID": str(uuid.uuid4()),
        "User-Agent": "AnimeContentBot/1.0"
    }
    payload = {
        "model": "GigaChat-3-Ultra",
        "messages": [
            {"role": "system", "content": "Ты — эксперт по аниме."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens
    }
    try:
        response = requests.post(
            "https://api.giga.chat/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Ошибка GigaChat: {e}")
        return ""

def generate_description(anime_name, token):
    prompt = f"Опиши аниме «{anime_name}» в одном-двух предложениях, кратко, на русском языке. Не добавляй название, только описание."
    desc = giga_request(prompt, token, max_tokens=200)
    desc = re.sub(r'^«[^»]+»\s*[—\-:]\s*', '', desc)
    if desc:
        desc = desc[0].lower() + desc[1:]

    sentences = re.split(r'(?<=[.!?])\s+', desc)
    if len(sentences) > 2:
        desc = ' '.join(sentences[:2]).strip()
        if desc and desc[-1] not in '.!?':
            desc += '.'
    return desc

def get_anime_meta(anime_name, token):
    prompt = (
        f"Назови жанр (жанры), количество серий и статус аниме «{anime_name}».\n"
        "Если жанров несколько, перечисли их через запятую.\n"
        "Если количество серий неизвестно, напиши '?'.\n"
        "Статус укажи одним словом: закончено, онгоинг или анонс.\n"
        "Выведи ответ строго по одному пункту на строку:\n"
        "Жанр: <жанры>\n"
        "Серии: <число или ?>"
        "Статус: <закончено|онгоинг|анонс|неизвестно>"
    )
    content = giga_request(prompt, token, max_tokens=150)

    genres = ""
    episodes = ""
    status = ""

    m = re.search(r'Жанр:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if m:
        genres = m.group(1).strip()

    m = re.search(r'Серии:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if m:
        episodes = m.group(1).strip()

    m = re.search(r'Статус:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
    if m:
        status = m.group(1).strip()

    if episodes and 'Статус:' in episodes:
        episodes = re.sub(r'Статус:.*$', '', episodes).strip()
    if status and 'Статус:' in status:
        status = re.sub(r'Статус:\s*', '', status).strip()

    return genres, episodes, status

def episodes_word(episodes):
    try:
        n = int(episodes)
    except:
        return f"{episodes} серий"
    if 10 <= n % 100 <= 20:
        return f"{n} серий"
    if n % 10 == 1:
        return f"{n} серия"
    if 2 <= n % 10 <= 4:
        return f"{n} серии"
    return f"{n} серий"

def generate_recommendations():
    all_anime = load_top_anime()
    if not all_anime:
        print("Список аниме пуст")
        return None

    used_anime = load_used_anime()

    available = [a for a in all_anime if a.lower() not in used_anime]
    if len(available) < 3:
        print("Недостаточно новых аниме, начинаем использовать повторы")
        available = all_anime

    chosen = random.sample(available, 3)

    token = get_gigachat_token()
    if not token:
        print("Не удалось получить токен GigaChat")
        return None

    cards = []
    for idx, name in enumerate(chosen):
        desc = generate_description(name, token)
        if not desc:
            print(f"Не удалось получить описание для '{name}'")
            return None

        print(f"Запрос метаданных для '{name}'...")
        genres, episodes, status = get_anime_meta(name, token)

        genres_str = genres if genres else "жанр не указан"
        episodes_str = episodes_word(episodes) if episodes and episodes != "?" else "кол-во серий неизвестно"
        status_str = status if status and status != "неизвестно" else "статус неизвестен"

        meta = f"{genres_str}, {episodes_str}, {status_str}"
        meta = meta.capitalize()

        emoji = ITEM_EMOJI[idx % len(ITEM_EMOJI)]

        # Формат: эмодзи «Название» — описание
        # Затем цитата с метаданными
        card = f"{emoji} <b>«{name}»</b> — {desc}\n<blockquote>[{meta}]</blockquote>"
        if idx < len(chosen) - 1:
            card += "\n────────────────"
        cards.append(card)

    used_anime.update(chosen)
    save_used_anime(used_anime)

    return '\n'.join(cards)

def send_recommendation_post(cards):
    header = "✨ <b>Рубрика: аниме, которые стоит посмотреть</b>"
    message = f"{header}\n\n{cards}\n\n#аниме #рекомендации #чтопосмотреть"
    try:
        bot.send_message(CHANNEL_ID, message, parse_mode='HTML', disable_web_page_preview=True)
        print("Рекомендации опубликованы.")
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def main():
    cards = generate_recommendations()
    if cards:
        send_recommendation_post(cards)
    else:
        print("Не удалось сгенерировать рекомендации.")

if __name__ == "__main__":
    main()
