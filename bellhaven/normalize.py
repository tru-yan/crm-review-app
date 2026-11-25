"""Normalization for comparing website data with CRM data. Comparison only: never write these values."""
import re

_STREET_WORDS = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "boulevard": "blvd",
    "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy", "highway": "hwy",
    "pk": "pike", "pke": "pike",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
}
# Whole words only: "Ste" is a suite, but "Stewart" is a street name.
_UNIT = re.compile(r"(?:\b(?:suite|ste|unit|apt)\b\.?|#)\s*[\w-]+", re.IGNORECASE)
_PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.IGNORECASE)

CARE_TYPE_MAP = {
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Independent Living": "Independent Living",
}


def norm_street(street: str) -> str:
    s = _UNIT.sub(" ", street or "")
    s = re.sub(r"[.,]", " ", s.lower())
    return " ".join(_STREET_WORDS.get(word, word) for word in s.split())


def is_po_box(street: str) -> bool:
    return bool(_PO_BOX.match(street or ""))


def zip5(zip_code: str) -> str:
    return re.sub(r"\D", "", zip_code or "")[:5]


def norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else ""


def norm_name(name: str) -> str:
    """For similarity scoring only. Name *changes* use same_text, because any wording change is real."""
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"\bhealth care\b", "healthcare", n)
    n = re.sub(r"\bcentre\b", "center", n)
    n = re.sub(r"\brehab\b", "rehabilitation", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return " ".join(n.split())


def same_text(a: str, b: str) -> bool:
    return " ".join((a or "").split()).lower() == " ".join((b or "").split()).lower()


def crm_care_types(offerings) -> list[str]:
    return [CARE_TYPE_MAP.get(o, o) for o in offerings]
