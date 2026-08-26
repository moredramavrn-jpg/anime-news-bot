import requests
import time
import os
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TOP_ANIME_FILE = "top_anime.txt"
POPULAR_ANIME_FILE = "popular_anime.txt"
EXCLUDE_FILE = "exclude.txt"
MAX_ANIME = 2000
TARGET_POPULAR = 100
NEW_LIMIT = 50
TOTAL_PAGES = 80
LIMIT_PER_PAGE = 50

BAD_SUBSTRINGS = [
    "спецвыпуск", "специальный", "фильм", "сезон", "часть", "ova", "ona",
    "спин-офф", "дополнение", "эпизод", "продолжение", "заключительная",
    "special", "movie", "season", "part", "episode", "final", "решающая игра"
]

HEADERS = {
    "User-Agent": "AnimeTopFetcher/1.0",   # имя приложения, как требует Shikimori
    "Accept": "application/json",
    "Content-Type": "application/json"
}

GRAPHQL_URL = "https://shikimori.one/api/graphql"

def requests_retry_session(
    retries=3,
    backoff_factor=1,
    status_forcelist=(500, 502, 503, 504),
    session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def load_exclude_list():
    exclude = set()
    if os.path.exists(EXCLUDE_FILE):
        with open(EXCLUDE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                name = line.strip().lower()
                if name:
                    exclude.add(name)
    return exclude

def clean_title(title, exclude_set=None):
    """Очищает название, убирая сезоны, спецвыпуски и т.п."""
    if not title:
        return None

    if '!! ' in title:
        title = title.split('!! ')[0] + '!!'

    for sep in ['—', ' - ', ' – ']:
        if sep in title:
            title = title.split(sep)[0].strip()
            break

    title = re.sub(
        r'\s+\d+\.?\s*(?:часть|сезон|part|season|special|спецвыпуск)\s*$',
        '',
        title,
        flags=re.IGNORECASE
    ).strip()

    if re.search(r'\d\.\d', title):
        return None

    title = re.sub(r'(\s|-)\d+$', '', title).strip()

    title = re.sub(
        r'(?:\s|-)M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$',
        '',
        title,
        flags=re.IGNORECASE
    ).strip()

    title = re.sub(r'\s*[xх]\s*UT\s*$', '', title, flags=re.IGNORECASE).strip()

    if len(title) < 2:
        return None

    lower = title.lower()
    if exclude_set and lower in exclude_set:
        return None

    for bad in BAD_SUBSTRINGS:
        if bad in lower:
            return None

    return ' '.join(title.split())

def fetch_shikimori_released(total_pages=TOTAL_PAGES, limit=LIMIT_PER_PAGE, exclude_set=None):
    """Собирает полный список завершённых аниме через REST API Shikimori."""
    names = []
    session = requests_retry_session()
    url = "https://shikimori.one/api/animes"
    params = {
        "order": "ranked",
        "kind": "tv,movie,ova,ona,special",
        "status": "released",
        "rating": "g,pg,pg_13,r,r_plus",
        "limit": limit,
        "page": 1,
    }

    if exclude_set is None:
        exclude_set = set()

    for page in range(1, total_pages + 1):
        params["page"] = page
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
            if not data:
                print(f"Shikimori: страница {page} пуста, останавливаемся.")
                break
            for anime in data:
                name = anime.get("russian") or anime.get("english") or anime.get("name")
                name = clean_title(name, exclude_set)
                if name:
                    names.append(name)
            print(f"Shikimori: получена страница {page} ({len(data)} записей)")
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка Shikimori на странице {page}: {e}")
            time.sleep(2)
            continue
    return names

def fetch_popular_candidates_graphql(limit=NEW_LIMIT):
    """Получает свежие популярные завершённые TV-аниме через GraphQL."""
    query = """
    query ($page: Int, $limit: Int) {
      animes(page: $page, limit: $limit, order: popularity, kind: "tv", status: "released") {
        name
        russian
        english
      }
    }
    """
    variables = {
        "page": 1,
        "limit": limit
    }

    names = []
    try:
        session = requests_retry_session()
        response = session.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            print(f"Ошибки GraphQL: {data['errors']}")
            return []

        animes = data.get("data", {}).get("animes", [])
        for anime in animes:
            name = anime.get("russian") or anime.get("english") or anime.get("name")
            name = clean_title(name)
            if name:
                names.append(name)
    except Exception as e:
        print(f"Ошибка получения популярных с Shikimori GraphQL: {e}")
    return names

def load_existing_popular():
    """Читает текущий popular_anime.txt (сохраняет порядок)."""
    if not os.path.exists(POPULAR_ANIME_FILE):
        return []
    with open(POPULAR_ANIME_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def save_top_anime(names):
    """Сохраняет полный список в top_anime.txt."""
    unique = []
    seen = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(name)
            if len(unique) >= MAX_ANIME:
                break

    with open(TOP_ANIME_FILE, 'w', encoding='utf-8') as f:
        for name in unique:
            f.write(name + '\n')
    print(f"Сохранено {len(unique)} аниме в {TOP_ANIME_FILE}")

def update_popular_anime(existing_popular, popular_candidates, target=TARGET_POPULAR):
    """Дополняет popular_anime.txt новыми названиями, сохраняя существующие."""
    existing_set = {name.lower() for name in existing_popular}
    added = []
    for name in popular_candidates:
        if name.lower() not in existing_set:
            added.append(name)
            existing_set.add(name.lower())
            if len(existing_popular) + len(added) >= target:
                break
    final_list = existing_popular + added

    with open(POPULAR_ANIME_FILE, 'w', encoding='utf-8') as f:
        for name in final_list:
            f.write(name + '\n')
    print(f"Обновлён {POPULAR_ANIME_FILE}: было {len(existing_popular)}, добавлено {len(added)}, итого {len(final_list)}")

def main():
    exclude_set = load_exclude_list()
    print(f"Загружено исключений: {len(exclude_set)}")

    print(f"=== Сбор полного списка с Shikimori (REST) ===")
    full_names = fetch_shikimori_released(total_pages=TOTAL_PAGES, limit=LIMIT_PER_PAGE, exclude_set=exclude_set)
    print(f"Shikimori: собрано {len(full_names)} названий до дедупликации")
    save_top_anime(full_names)

    print(f"\n=== Обновление популярных аниме (GraphQL) ===")
    existing_popular = load_existing_popular()
    print(f"Текущий популярный список: {len(existing_popular)} названий")

    popular_candidates = fetch_popular_candidates_graphql(limit=NEW_LIMIT)
    print(f"Получено кандидатов для пополнения: {len(popular_candidates)}")

    update_popular_anime(existing_popular, popular_candidates, target=TARGET_POPULAR)

if __name__ == "__main__":
    main()
