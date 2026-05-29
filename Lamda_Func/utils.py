# -*- coding: utf-8 -*-

def format_severity(value: str) -> str:
    """
    Normalize severity input from Alexa slots.
    - Accepts numbers (1–10) as string
    - Accepts German words: leicht, mittel, stark
    - Returns standardized string ("1", "2", ..., "10", "leicht", "mittel", "stark")
    - Returns None if not valid
    """
    if not value:
        return None

    s = value.strip().lower()

    # map common german words
    word_map = {
        "leicht": "leicht",
        "gering": "leicht",
        "mittel": "mittel",
        "mittelmäßig": "mittel",
        "stark": "stark",
        "schwer": "stark"
    }
    if s in word_map:
        return word_map[s]

    # try to parse number
    try:
        n = int(s)
        if 1 <= n <= 10:
            return str(n)
    except ValueError:
        pass

    return None
