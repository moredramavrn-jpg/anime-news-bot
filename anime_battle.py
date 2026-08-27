import os
import random
import requests
import uuid
import time
import urllib3
import telebot
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

CHARACTERS_FILE = "characters.txt"

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

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

def get_russian_name(text):
    print(f"Ищу русское название для: {text}")

    if not GIGACHAT_AUTHORIZATION_KEY:
        print("GigaChat: нет ключа авторизации")
        return text

    token = get_gigachat_token()
    if not token:
        print("GigaChat: не удалось получить токен")
        return text

    prompt = (
        f"Найди официальное русское название (или устоявшееся в русскоязычном аниме-сообществе) для: «{text}».\n"
        "Если это имя персонажа, верни его так, как его обычно пишут по-русски (например, 'Eren Yeager' -> 'Эрен Йегер').\n"
        "Если это название аниме, верни русское название (например, 'Attack on Titan' -> 'Атака титанов').\n"
        "Выведи только итоговое русское название/имя без пояснений."
    )
    try:
        response = requests.post(
            "https://api.giga.chat/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Request-ID": str(uuid.uuid4()),
                "X-Session-ID": str(uuid.uuid4()),
                "User-Agent": "AnimeBattleBot/1.0"
            },
            json={
                "model": "GigaChat-3-Ultra",
                "messages": [
                    {"role": "system", "content": "Ты — эксперт по аниме и знаешь официальные русские названия и имена."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 200
            },
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        data = response.json()
        russian = data["choices"][0]["message"]["content"].strip()
        print(f"Получено: {russian}")
        if russian and russian.lower() != text.lower():
            return russian
    except Exception as e:
        print(f"Ошибка получения русского названия: {e}")
    return text

def load_characters():
    if not os.path.exists(CHARACTERS_FILE):
        print(f"Файл {CHARACTERS_FILE} не найден")
        return []

    characters = []
    with open(CHARACTERS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 3:
                continue
            characters.append({
                'name': parts[0].strip(),
                'anime': parts[1].strip(),
                'image_url': parts[2].strip()
            })
    return characters

def download_image(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return BytesIO(r.content)
    except Exception as e:
        print(f"Ошибка скачивания изображения {url}: {e}")
        return None

def create_collage(img1_bytes, img2_bytes):
    try:
        img1 = Image.open(img1_bytes)
        img2 = Image.open(img2_bytes)

        height = 600
        img1 = img1.resize((int(img1.width * height / img1.height), height))
        img2 = img2.resize((int(img2.width * height / img2.height), height))

        vs_space = 150
        collage_width = img1.width + img2.width + vs_space
        collage_height = height

        collage = Image.new("RGB", (collage_width, collage_height), "white")
        collage.paste(img1, (0, 0))
        collage.paste(img2, (img1.width + vs_space, 0))

        draw = ImageDraw.Draw(collage)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 100)
        except:
            font = ImageFont.load_default()

        text = "VS"
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        text_x = img1.width + (vs_space - text_width) // 2
        text_y = (collage_height - text_height) // 2
        draw.text((text_x, text_y), text, fill="black", font=font)

        output = BytesIO()
        collage.save(output, format="JPEG")
        output.seek(0)
        return output
    except Exception as e:
        print(f"Ошибка создания коллажа: {e}")
        return None

def send_battle(char1, char2, collage_bytes):
    print("Получаю русские названия...")
    name1 = get_russian_name(char1['name'])
    anime1 = get_russian_name(char1['anime'])
    name2 = get_russian_name(char2['name'])
    anime2 = get_russian_name(char2['anime'])

    caption = (
        f"⚔️ <b>Аниме-баттл!</b>\n\n"
        f"Сегодня сражаются:\n\n"
        f"<b>─── {name1}</b> (<i>{anime1}</i>)\n"
        f" <b>🆚</b>\n"
        f"<b>─── {name2}</b> (<i>{anime2}</i>)\n\n"
        f"Кто победит? Голосуйте реакциями:\n"
        f"👍 — за {name1}\n"
        f"🔥 — за {name2}\n\n"
        f"#аниме #баттл #голосование"
    )
    try:
        bot.send_photo(
            CHANNEL_ID,
            collage_bytes,
            caption=caption,
            parse_mode='HTML'
        )
        print(f"Баттл опубликован: {name1} vs {name2}")
    except Exception as e:
        print(f"Ошибка отправки баттла: {e}")

def main():
    characters = load_characters()
    if len(characters) < 2:
        print("Недостаточно персонажей для баттла (нужно минимум 2)")
        return

    char1, char2 = random.sample(characters, 2)

    print(f"Скачиваю изображение для {char1['name']}...")
    img1_bytes = download_image(char1['image_url'])
    if not img1_bytes:
        print("Не удалось скачать первое изображение")
        return

    print(f"Скачиваю изображение для {char2['name']}...")
    img2_bytes = download_image(char2['image_url'])
    if not img2_bytes:
        print("Не удалось скачать второе изображение")
        return

    print("Создаю коллаж...")
    collage_bytes = create_collage(img1_bytes, img2_bytes)
    if not collage_bytes:
        print("Не удалось создать коллаж")
        return

    send_battle(char1, char2, collage_bytes)

if __name__ == "__main__":
    main()
