import os
import re
import random
import time
import uuid
import io
import urllib3
import telebot
import requests
import subprocess

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Токены и ID
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
GIGACHAT_AUTHORIZATION_KEY = os.getenv("GIGACHAT_AUTHORIZATION_KEY")

# === ЖЕСТКАЯ ПРИВЯЗКА ПУТЕЙ К ПАПКЕ СКРИПТА ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POPULAR_ANIME_FILE = os.path.join(BASE_DIR, "popular_anime.txt")
LAST_QUIZ_TYPE_FILE = os.path.join(BASE_DIR, "last_quiz_type.txt")
LAST_MEDIA_TYPE_FILE = os.path.join(BASE_DIR, "last_media_type.txt")
# ==============================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

gigachat_access_token = None
gigachat_token_expires_at = 0

# ==========================================
# 1. РАБОТА С GIGACHAT
# ==========================================

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
    
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, data=data, timeout=30, verify=False)
            r.raise_for_status()
            token_data = r.json()
            gigachat_access_token = token_data.get("access_token")
            expires_at = token_data.get("expires_at")
            gigachat_token_expires_at = (expires_at / 1000 if expires_at > 10**12 else expires_at) if expires_at else time.time() + 1800
            return gigachat_access_token
        except Exception as e:
            print(f"Попытка {attempt + 1}: Ошибка токена GigaChat: {e}")
            time.sleep(3)
    return None

def giga_request(prompt, token, max_tokens=300):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-ID": str(uuid.uuid4()),
        "User-Agent": "AnimeQuizBot/10.6"
    }
    payload = {
        "model": "GigaChat-3-Ultra",
        "messages": [
            {
                "role": "system", 
                "content": (
                    "Ты — харизматичный ведущий викторины по аниме. "
                    "Генерируй ровно один короткий вопрос без вводных слов. "
                    "Внимательно следуй ограничениям в запросе пользователя."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.8,
        "max_tokens": max_tokens
    }
    
    for attempt in range(3):
        try:
            response = requests.post("https://api.giga.chat/v1/chat/completions", headers=headers, json=payload, timeout=30, verify=False)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"Попытка {attempt + 1}: Ошибка GigaChat (генерация): {e}")
            time.sleep(3)
    return ""

def clean_question(text):
    if not text: return ""
    text = re.sub(r'Как и любая языковая модель.*?информация\.', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'Ответ сгенерирован нейросетевой моделью.*?информация\.', '', text, flags=re.IGNORECASE | re.DOTALL)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if lines:
        question = " ".join(lines)
        return re.sub(r'\.{3,}$', '', question).strip()
    return ""

def is_answer_in_question(question, answer_text):
    q_words = set(re.findall(r'[а-яёa-z0-9]+', question.lower()))
    a_words = set(re.findall(r'[а-яёa-z0-9]+', answer_text.lower()))
    a_words = {w for w in a_words if len(w) > 2}
    return bool(q_words.intersection(a_words))

# ==========================================
# 2. ПОЛУЧЕНИЕ МЕДИАФАЙЛОВ И ПЕРСОНАЖЕЙ
# ==========================================

def get_shikimori_info(anime_name):
    try:
        url = f"https://shikimori.one/api/animes?search={anime_name}&limit=1"
        res = requests.get(url, headers={"User-Agent": "AnimeQuizBot"}, timeout=15).json()
        if res and isinstance(res, list) and len(res) > 0:
            return res[0]['id'], res[0]['name']
    except: pass
    return None, None

def get_anime_characters(anime_id):
    try:
        url = f"https://shikimori.one/api/animes/{anime_id}/roles"
        res = requests.get(url, headers={"User-Agent": "AnimeQuizBot"}, timeout=15).json()
        chars = [r['character'] for r in res if 'character' in r and r['character']]
        return chars
    except: pass
    return []

def fetch_anime_image(anime_id):
    try:
        url = f"https://shikimori.one/api/animes/{anime_id}/screenshots"
        res = requests.get(url, headers={"User-Agent": "AnimeQuizBot"}, timeout=15).json()
        if res and isinstance(res, list) and len(res) > 0:
            return "https://shikimori.one" + random.choice(res)['original']
    except: pass
    return None

def fetch_anime_audio(romaji_name):
    try:
        url = f"https://api.animethemes.moe/anime?q={romaji_name}&include=animethemes.animethemeentries.videos.audio"
        res = requests.get(url, timeout=20).json()
        if not res.get('anime'): return None
        for op in [t for t in res['anime'][0]['animethemes'] if t['type'] == 'OP']:
            for entry in op.get('animethemeentries', []):
                for video in entry.get('videos', []):
                    if video.get('audio', {}).get('link'): return video['audio']['link']
    except: pass
    return None

def fetch_anime_video(romaji_name):
    try:
        url = f"https://api.animethemes.moe/anime?q={romaji_name}&include=animethemes.animethemeentries.videos"
        res = requests.get(url, timeout=20).json()
        if not res.get('anime'): return None
        ops = [t for t in res['anime'][0]['animethemes'] if t['type'] in ['OP', 'ED']]
        for op in ops:
            for entry in op.get('animethemeentries', []):
                for video in entry.get('videos', []):
                    if video.get('link'): return video['link']
    except: pass
    return None

# ==========================================
# 3. ФИЛЬТРЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def load_last_value(filename):
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f: return f.read().strip()
    return None

def save_last_value(filename, value):
    try:
        with open(filename, 'w', encoding='utf-8') as f: f.write(value)
        print(f"[DEBUG] Сохранено: '{value}' в {os.path.basename(filename)}")
    except Exception as e: print(f"[ERROR] Сохранение файла {filename}: {e}")

def load_popular_anime():
    if not os.path.exists(POPULAR_ANIME_FILE): return []
    with open(POPULAR_ANIME_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def get_base_name(name):
    base = name.lower().replace('re:zero', 'rezero')
    base = re.split(r':\s+| - | — |\.\s+', base)[0]
    return re.sub(r'\s*\(\d{4}\)|\s+(фильм|сезон|часть|ova|ona|movie|tv).*|\s+\d+$', '', base).strip()

def are_same_franchise(name1, name2):
    b1, b2 = get_base_name(name1), get_base_name(name2)
    if b1 == b2 or (len(b1) > 4 and len(b2) > 4 and (b1.startswith(b2 + " ") or b2.startswith(b1 + " "))): return True
    if "джоджо" in b1 and "джоджо" in b2: return True
    return False

def format_display_name(name):
    base = re.split(r':\s+| - | — |\.\s+', name)[0]
    return re.sub(r'\s*\(\d{4}\)|\s+(Фильм|Сезон|Часть|OVA|ONA|Movie|TV|фильм|сезон|часть).*', '', base).strip()

# ==========================================
# 4. ГЕНЕРАЦИЯ ВОПРОСОВ
# ==========================================

def generate_strict_quiz(search_name, display_name, token, target_media, anime_id=None, char_img=None, romaji_name=None):
    if target_media == "image":
        url = fetch_anime_image(anime_id)
        if not url: return None, None
        save_last_value(LAST_QUIZ_TYPE_FILE, "угадай по кадру")
        return random.choice(["Откуда этот кадр?", "Проверим зрительную память! Из какого тайтла скриншот?", "К какому аниме принадлежит этот кадр?"]), url
        
    elif target_media == "audio":
        url = fetch_anime_audio(romaji_name)
        if not url: return None, None 
        save_last_value(LAST_QUIZ_TYPE_FILE, "угадай опенинг")
        return random.choice(["Узнаете эти ноты? Из какого аниме опенинг?", "Слушаем и угадываем! Откуда трек?"]), url
        
    elif target_media == "video":
        url = fetch_anime_video(romaji_name)
        if not url: return None, None
        save_last_value(LAST_QUIZ_TYPE_FILE, "угадай по видео")
        return random.choice(["Узнаете этот визуальный стиль? Откуда отрывок?", "Напрягаем память! Из какого аниме этот видеоряд?"]), url

    elif target_media == "image_char":
        save_last_value(LAST_QUIZ_TYPE_FILE, "угадай персонажа по фото")
        return random.choice(["Узнаете это лицо? Как зовут персонажа?", "Проверим память на лица! Кто изображен на арте?", "Один взгляд — и всё ясно. Кто это?"]), char_img

    else: 
        # 8 СТРОГИХ ФОРМАТОВ
        templates = [
            {"type": "завязка сюжета", "prompt": f"Напиши интригующее описание завязки сюжета аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать имена персонажей и название аниме. Закончи вопросом 'Из какого это аниме?'. СТРОГО до 200 символов."},
            {"type": "устройство мира", "prompt": f"Опиши уникальные правила мира, вселенной или местную систему сил/магии из аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать имена героев и название аниме. Закончи вопросом 'Где существуют такие законы?'. СТРОГО до 200 символов."},
            {"type": "главная цель", "prompt": f"Опиши главную цель, проклятие или миссию главного героя аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО называть любые имена и само название аниме. Закончи вопросом 'Откуда этот сюжет?'. СТРОГО до 200 символов."},
            {"type": "знаковое событие", "prompt": f"Кратко опиши очень известное событие, испытание или битву из аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать имена участников и название тайтла. Закончи вопросом 'Где это произошло?'. СТРОГО до 200 символов."},
            {"type": "план злодея", "prompt": f"Опиши глобальный план или безумную философию главного злодея (или вражеской организации) из аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО называть имена и название аниме. Закончи вопросом 'Чей мир в опасности?'. СТРОГО до 200 символов."},
            {"type": "знаковая локация", "prompt": f"Атмосферно опиши главную локацию, школу, город или мир, где происходят основные события аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО называть имена и название аниме. Закончи вопросом 'Где находится это место?'. СТРОГО до 200 символов."},
            {"type": "жизненное кредо", "prompt": f"Сформулируй главное жизненное кредо, обещание или самую известную философию протагониста из аниме «{display_name}» своими словами. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать имена и название аниме. Закончи вопросом 'Кто следует этому пути?'. СТРОГО до 200 символов."},
            {"type": "главная угроза", "prompt": f"Опиши главную нечеловеческую угрозу, монстров или аномалию, с которой вынуждены бороться герои в аниме «{display_name}». КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать оригинальные термины из сериала, имена и название аниме. Закончи вопросом 'Откуда эти создания?'. СТРОГО до 200 символов."}
        ]
        last_type = load_last_value(LAST_QUIZ_TYPE_FILE)
        available = [t for t in templates if t["type"] != last_type] or templates
        template = random.choice(available)
        
        for _ in range(3):
            q = clean_question(giga_request(template["prompt"], token, max_tokens=250))
            if q and not (is_answer_in_question(q, display_name) or is_answer_in_question(q, search_name)):
                save_last_value(LAST_QUIZ_TYPE_FILE, template["type"])
                return q, None
            time.sleep(3)
        return None, None

# ==========================================
# 5. ОТПРАВКА И ГЛАВНЫЙ ЦИКЛ
# ==========================================

def send_quiz_poll(question_text, options, correct_index, media_url=None, media_type="text"):
    header = "🎌 Аниме-викторина\n\n"
    full_question = f"{header}{question_text}"
    if len(full_question) > 300: full_question = full_question[:297] + "..."

    # ЗАЩИТА ОТ ДЛИННЫХ ВАРИАНТОВ ОТВЕТА (Лимит Telegram - 100 символов)
    safe_options = [opt[:97] + "..." if len(opt) > 100 else opt for opt in options]

    try:
        media_msg = None
        if media_type in ["image", "image_char"] and media_url:
            print("[DEBUG] Скачиваю картинку...")
            img_data = requests.get(media_url, headers={"User-Agent": "AnimeQuizBot"}, timeout=20).content
            media_msg = bot.send_photo(chat_id=CHANNEL_ID, photo=img_data)
            
        elif media_type == "audio" and media_url:
            print("[DEBUG] Скачиваю аудио...")
            audio_data = requests.get(media_url, headers={"User-Agent": "AnimeQuizBot"}, timeout=30).content
            audio_file = io.BytesIO(audio_data)
            audio_file.name = "opening.ogg"
            media_msg = bot.send_audio(chat_id=CHANNEL_ID, audio=audio_file)
            
        elif media_type == "video" and media_url:
            print("[DEBUG] Скачиваю видео...")
            with open("temp.webm", "wb") as f: f.write(requests.get(media_url, headers={"User-Agent": "AnimeQuizBot"}, timeout=60).content)
            start_sec = random.randint(35, 65)
            print(f"[DEBUG] Конвертирую видео, старт с {start_sec} сек...")
            subprocess.run(["ffmpeg", "-y", "-i", "temp.webm", "-ss", str(start_sec), "-t", "15", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "temp.mp4"], check=True)
            with open("temp.mp4", "rb") as f: media_msg = bot.send_video(chat_id=CHANNEL_ID, video=f)
            if os.path.exists("temp.webm"): os.remove("temp.webm")
            if os.path.exists("temp.mp4"): os.remove("temp.mp4")

        bot.send_poll(
            chat_id=CHANNEL_ID, question=full_question, options=safe_options, type="quiz",
            correct_option_id=correct_index, open_period=86400, is_anonymous=True,
            reply_to_message_id=media_msg.message_id if media_msg else None
        )
        print(f"[SUCCESS] Викторина опубликована. Тип: {media_type}")
    except Exception as e: print(f"[ERROR] Ошибка отправки: {e}")

def main():
    all_anime = load_popular_anime()
    if len(all_anime) < 4: return print("[ERROR] Мало тайтлов в popular_anime.txt")

    token = get_gigachat_token()
    if not token: return print("[ERROR] Токен GigaChat не получен")

    last_media = load_last_value(LAST_MEDIA_TYPE_FILE)
    
    # 5 форматов по кругу
    sequence = {
        "text": "image", 
        "image": "audio", 
        "audio": "video", 
        "video": "image_char", 
        "image_char": "text"
    }
    target_media = sequence.get(last_media, "text")
    print(f"[DEBUG] Целевой формат: '{target_media}'")

    quiz_data = None
    for attempt in range(10):
        correct_anime = random.choice(all_anime)
        display_correct_anime = format_display_name(correct_anime)
        anime_id, romaji_name = get_shikimori_info(correct_anime)
        if not anime_id: continue
        
        wrong_anime_list = []
        shuffled = all_anime.copy(); random.shuffle(shuffled)
        for a in shuffled:
            if are_same_franchise(a, correct_anime): continue
            if not any(are_same_franchise(a, w) for w in wrong_anime_list):
                wrong_anime_list.append(a)
            if len(wrong_anime_list) == 3: break
        if len(wrong_anime_list) < 3: continue

        if target_media == "image_char":
            chars = get_anime_characters(anime_id)
            if not chars: continue
            
            correct_char = random.choice(chars[:15])
            char_name = correct_char.get('russian') or correct_char.get('name')
            char_img = "https://shikimori.one" + correct_char['image']['original'] if correct_char.get('image') else None
            
            if not char_img or 'missing' in char_img: continue

            # Собираем неверные варианты с добавлением названия аниме
            wrong_chars = []
            for wa in wrong_anime_list:
                w_id, _ = get_shikimori_info(wa)
                w_chars = get_anime_characters(w_id)
                display_wa = format_display_name(wa)
                
                if w_chars:
                    wc = random.choice(w_chars[:10])
                    wc_name = wc.get('russian') or wc.get('name')
                    opt = f"{wc_name} ({display_wa})"
                else: 
                    opt = f"Неизвестный Герой ({display_wa})"
                
                wrong_chars.append(opt[:97] + "..." if len(opt) > 100 else opt)

            opt_correct = f"{char_name} ({display_correct_anime})"
            display_correct = opt_correct[:97] + "..." if len(opt_correct) > 100 else opt_correct
            display_wrongs = wrong_chars
            
            question, media_url = generate_strict_quiz(correct_anime, display_correct_anime, token, target_media, anime_id, char_img=char_img, romaji_name=romaji_name)

        else:
            display_correct = display_correct_anime
            display_wrongs = [format_display_name(w) for w in wrong_anime_list]
            question, media_url = generate_strict_quiz(correct_anime, display_correct_anime, token, target_media, anime_id, romaji_name=romaji_name)
        
        if question:
            quiz_data = (display_correct, display_wrongs, question, media_url)
            break

    if not quiz_data: return print(f"[ERROR] Не удалось создать викторину '{target_media}'")

    final_correct, final_wrongs, question, media_url = quiz_data
    options = [final_correct] + final_wrongs
    random.shuffle(options)
    correct_index = options.index(final_correct)

    send_quiz_poll(question, options, correct_index, media_url, target_media)
    save_last_value(LAST_MEDIA_TYPE_FILE, target_media)

if __name__ == "__main__":
    main()
