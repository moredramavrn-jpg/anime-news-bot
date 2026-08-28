import os
import re
import random
import time
import uuid
import urllib3
import telebot
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

POPULAR_ANIME_FILE = "popular_anime.txt"
LAST_QUIZ_TYPE_FILE = "last_quiz_type.txt"   # файл для хранения последнего типа вопроса

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

def load_last_quiz_type():
    """Читает тип последнего вопроса из файла."""
    if os.path.exists(LAST_QUIZ_TYPE_FILE):
        with open(LAST_QUIZ_TYPE_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    return None

def save_last_quiz_type(qtype):
    """Сохраняет тип последнего вопроса в файл."""
    with open(LAST_QUIZ_TYPE_FILE, 'w', encoding='utf-8') as f:
        f.write(qtype)

def load_popular_anime():
    if not os.path.exists(POPULAR_ANIME_FILE):
        print(f"Файл {POPULAR_ANIME_FILE} не найден")
        return []
    with open(POPULAR_ANIME_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def giga_request(prompt, token, max_tokens=300):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-ID": str(uuid.uuid4()),
        "User-Agent": "AnimeQuizBot/1.0"
    }
    payload = {
        "model": "GigaChat-3-Ultra",
        "messages": [
            {"role": "system", "content": "Ты — ведущий викторины по аниме."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.8,
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

def generate_question(anime_name, token):
    """Генерирует вопрос одного из 50 типов, избегая повтора последнего."""
    question_templates = [
        # ... (весь список из 50 шаблонов без изменений) ...
    ]

    # Если список пуст, вернём пустую строку
    if not question_templates:
        return ""

    last_type = load_last_quiz_type()
    # Исключаем последний тип, если есть другие варианты
    available = [t for t in question_templates if t["type"] != last_type]
    if not available:
        available = question_templates  # если все совпадают, берём все

    # Выбираем случайный шаблон
    template = random.choice(available)

    question = giga_request(template["prompt"], token, max_tokens=250)

    # Убираем возможное многоточие в конце
    question = re.sub(r'\.{3,}$', '', question).strip()

    # Если вопрос слишком длинный, обрезаем без добавления многоточия
    if len(question) > 250:
        question = question[:250].rsplit(' ', 1)[0].strip()

    # Сохраняем тип вопроса
    save_last_quiz_type(template["type"])

    return question

def send_quiz_poll(question_text, options, correct_index):
    header = "🎌 Аниме-викторина\n\n"
    full_question = f"{header}{question_text}"

    # Telegram допускает максимум 300 символов в вопросе
    if len(full_question) > 300:
        max_q_len = 300 - len(header)
        question_text = question_text[:max_q_len].rsplit(' ', 1)[0].strip()
        full_question = f"{header}{question_text}"

    try:
        bot.send_poll(
            chat_id=CHANNEL_ID,
            question=full_question,
            options=options,
            type="quiz",
            correct_option_id=correct_index,
            open_period=86400,          # 24 часа
            is_anonymous=True
        )
        print("Викторина опубликована.")
    except Exception as e:
        print(f"Ошибка отправки опроса: {e}")

def main():
    all_anime = load_popular_anime()
    if len(all_anime) < 4:
        print("Недостаточно названий для создания вариантов (нужно минимум 4)")
        return

    token = get_gigachat_token()
    if not token:
        print("Не удалось получить токен GigaChat")
        return

    correct_anime = random.choice(all_anime)
    wrong_pool = [a for a in all_anime if a.lower() != correct_anime.lower()]
    if len(wrong_pool) < 3:
        print("Недостаточно названий для создания вариантов")
        return
    wrong_answers = random.sample(wrong_pool, 3)

    question = generate_question(correct_anime, token)
    if not question:
        print("Не удалось сгенерировать вопрос")
        return

    options = [correct_anime] + wrong_answers
    random.shuffle(options)
    correct_index = options.index(correct_anime)

    send_quiz_poll(question, options, correct_index)

if __name__ == "__main__":
    main()
