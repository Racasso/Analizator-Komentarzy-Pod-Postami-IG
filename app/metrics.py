import unicodedata


PRICE_KEYWORDS = (
    "ile kosztuje",
    "jaka cena",
    "jaki koszt",
    "za ile",
    "kosztuje",
    "cena",
)
LINK_KEYWORDS = (
    "link",
    "podeslij",
    "poprosze link",
    "prosze o link",
    "wyslij link",
)
NEGATIVE_KEYWORDS = (
    "nie polecam",
    "beznadziej",
    "tragedia",
    "oszust",
    "rozczarow",
    "slabo",
    "nie dziala",
    "kiepsk",
    "za drogo",
    "drogo",
)


def normalize_text(text: str) -> str:
    folded = text.casefold().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def classify_comment(text: str) -> dict[str, bool]:
    normalized = normalize_text(text)
    return {
        "price_question": any(keyword in normalized for keyword in PRICE_KEYWORDS),
        "link_request": any(keyword in normalized for keyword in LINK_KEYWORDS),
        "negative": any(keyword in normalized for keyword in NEGATIVE_KEYWORDS),
    }
