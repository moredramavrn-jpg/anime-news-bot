import requests
import os

CHARACTERS_FILE = "characters.txt"
LIMIT = 100  # сколько персонажей сохранить

ANILIST_API = "https://graphql.anilist.co"

QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    characters(sort: FAVOURITES_DESC) {
      name {
        full
      }
      media(sort: POPULARITY_DESC, type: ANIME) {
        edges {
          node {
            title {
              romaji
              english
            }
          }
        }
      }
      image {
        large
      }
    }
  }
}
"""

def fetch_characters_from_anilist():
    characters = []
    page = 1
    while len(characters) < LIMIT:
        variables = {
            "page": page,
            "perPage": min(50, LIMIT - len(characters))
        }
        try:
            r = requests.post(
                ANILIST_API,
                json={"query": QUERY, "variables": variables},
                timeout=20
            )
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                print(f"Ошибки AniList: {data['errors']}")
                break

            items = data.get("data", {}).get("Page", {}).get("characters", [])
            if not items:
                break

            for ch in items:
                name = ch.get("name", {}).get("full", "")
                anime_name = ""
                media_edges = ch.get("media", {}).get("edges", [])
                if media_edges:
                    node = media_edges[0].get("node", {})
                    title = node.get("title", {})
                    anime_name = title.get("english") or title.get("romaji") or ""
                image_url = ch.get("image", {}).get("large", "")
                if name and image_url:
                    characters.append({
                        "name": name,
                        "anime": anime_name,
                        "image_url": image_url
                    })
        except Exception as e:
            print(f"Ошибка получения персонажей с AniList: {e}")
            break

        page += 1

    return characters[:LIMIT]

def save_characters(characters):
    with open(CHARACTERS_FILE, 'w', encoding='utf-8') as f:
        for ch in characters:
            f.write(f"{ch['name']}|{ch['anime']}|{ch['image_url']}\n")
    print(f"Сохранено {len(characters)} персонажей в {CHARACTERS_FILE}")

def main():
    print("Получаю популярных персонажей с AniList...")
    chars = fetch_characters_from_anilist()
    if chars:
        save_characters(chars)
    else:
        print("Не удалось получить персонажей")

if __name__ == "__main__":
    main()
