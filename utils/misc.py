from unidecode import unidecode


def normalize_text(text: str) -> str:
    return unidecode(text).lower().replace(" ", "").replace("\n", "")
