from html import escape


def safe_html(value: object, fallback: str = 'не указано') -> str:
    if value is None:
        return fallback

    text = str(value).strip()
    if not text:
        return fallback

    return escape(text)