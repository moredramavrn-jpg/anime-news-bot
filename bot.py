def format_news_body(text):
    """
    Преобразует текст новости в структурированный HTML:
    - Разбивает на абзацы (по 2 предложения, если исходный текст сплошной).
    - Выделяет названия в кавычках «...» жирным.
    - Первый абзац (лид) делает курсивом.
    """
    if not text:
        return ""

    # Нормализуем переносы строк
    text = re.sub(r'\n(?!\n)', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text).strip()

    # Если уже есть абзацы, берём их как есть
    if '\n\n' in text:
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    else:
        # Разбиваем на предложения
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if len(sentences) <= 1:
            paragraphs = [text]
        else:
            # Группируем по 2 предложения
            paragraphs = []
            current = []
            for sent in sentences:
                current.append(sent)
                if len(current) == 2:
                    paragraphs.append(" ".join(current))
                    current = []
            if current:
                paragraphs.append(" ".join(current))

    # Выделяем названия в кавычках «...» жирным
    def bold_quotes(s):
        return re.sub(r'«[^»]+»', lambda m: f"<b>{m.group(0)}</b>", s)

    paragraphs = [bold_quotes(p) for p in paragraphs]

    # Если абзацев больше одного, первый делаем курсивом (лид)
    if len(paragraphs) > 1:
        paragraphs[0] = f"<i>{paragraphs[0]}</i>"

    return "\n\n".join(paragraphs)


def escape_html(text):
    """Экранирует специальные символы для безопасного HTML."""
    return html.escape(text, quote=False)


def build_post_html(title, body, video_url=None, is_youtube=False):
    """Собирает красиво отформатированный HTML-текст поста."""
    title_esc = escape_html(title)
    body_formatted = format_news_body(body) if body else ""

    parts = [f"<b>{title_esc}</b>"]

    if body_formatted:
        parts.append("──────────")
        parts.append(body_formatted)

    if video_url and is_youtube:
        parts.append("")
        parts.append(f'🎬 <a href="{video_url}">Смотреть видео</a>')

    return "\n".join(parts)
