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
    """Генерирует вопрос одного из множества типов."""
    question_templates = [
        {
            "type": "описание сюжета",
            "prompt": (
                f"Составь вопрос для викторины об аниме «{anime_name}». "
                "Вопрос должен описывать сюжет или ключевые детали, но не называть само аниме. "
                "Начни с фразы: 'Какое аниме описывается так: ...'\n"
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "главный герой",
            "prompt": (
                f"Придумай вопрос для викторины о главном герое аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме главный герой...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "персонаж",
            "prompt": (
                f"Сформулируй вопрос для викторины, связанный с любым персонажем из аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме есть персонаж...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "опенинг",
            "prompt": (
                f"Составь вопрос для викторины про опенинг (музыкальную тему) аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме звучит опенинг...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "событие или сцена",
            "prompt": (
                f"Сформулируй вопрос для викторины о каком-либо событии или сцене из аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме происходит событие...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "цитата",
            "prompt": (
                f"Создай вопрос для викторины, в котором приводится цитата из аниме «{anime_name}». "
                "Вопрос должен выглядеть как 'Из какого аниме цитата: «...»?' "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "элемент мира",
            "prompt": (
                f"Составь вопрос для викторины о каком-либо уникальном элементе мира, предмете или концепции из аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме присутствует...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        },
        {
            "type": "сеттинг",
            "prompt": (
                f"Сформулируй вопрос для викторины о месте или времени действия аниме «{anime_name}». "
                "Вопрос должен начинаться с 'В каком аниме действие происходит...' или аналогично. "
                "Не называй само аниме в тексте вопроса. "
                "Не используй многоточие в конце вопроса. Выведи только текст вопроса."
            )
        }
    ]

    template = random.choice(question_templates)
    question = giga_request(template["prompt"], token, max_tokens=250)
    question = re.sub(r'\.{3,}$', '', question).strip()
    if len(question) > 250:
        question = question[:250].rsplit(' ', 1)[0].strip()
    return question

def send_quiz_poll(question_text, options, correct_index):
    header = "🎌 Аниме-викторина\n\n"
    full_question = f"{header}{question_text}"

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
            open_period=10800,          # 3 часа
            is_anonymous=True
        )
        print("Викторина опубликована.")
    except Exception as e:
        print(f"Ошибка отправки опроса: {e}")

def main():
    print("QUIZ STARTED", flush=True)
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
