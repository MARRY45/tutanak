"""Small text helpers: search folding, timestamps, JSON extraction."""

from __future__ import annotations

import json
import re
import unicodedata

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$")

# Folded (see ``fold``) Turkish and English function words, used only to turn a
# natural-language question into keyword search terms.
_STOPWORDS = frozenset(
    """
    ve veya ile ama fakat icin gibi kadar daha cok en de da ki mi mu bu su o bir ne neden nasil
    nerede kim kime hangi kac zaman hakkinda ilgili olarak olan oldu olur var yok mi midir
    dedi denildi soylendi edildi verildi alindi yapildi biz siz ben sen onlar neydi nedir
    the a an and or but for of to in on at by with about from as is are was were be been
    what when where who whom which why how did do does done has have had it this that these
    those we you they i he she said say
    """.split()
)


def fold(text: str) -> str:
    """Lowercase and strip diacritics so search is accent- and case-insensitive.

    Turkish needs care: ``I`` lowercases to ``ı`` and ``İ`` to ``i``. Both dotted and
    dotless forms are mapped to plain ``i`` so that "ISI", "ısı" and "isi" all match.
    """
    text = text.replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def query_terms(query: str) -> list[str]:
    """Folded, de-duplicated search terms (order preserved)."""
    seen: dict[str, None] = {}
    for term in words(fold(query)):
        seen.setdefault(term)
    return list(seen)


def content_terms(terms: list[str]) -> list[str]:
    """Drop question and filler words; keeps the input when nothing else is left."""
    kept = [t for t in terms if t not in _STOPWORDS and len(t) > 1]
    return kept or terms


def fts_query(terms: list[str], *, any_term: bool = False) -> str:
    """Build an FTS5 MATCH expression with prefix matching.

    Prefix matching matters for agglutinative languages: "bütçe" should also find
    "bütçeyi" and "bütçesini". Very short terms match exactly, otherwise "ne" would
    hit every word that starts with it.
    """
    joiner = " OR " if any_term else " "
    return joiner.join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in terms)


def snippet(text: str, terms: list[str], width: int = 220) -> str:
    """Cut ``text`` to roughly ``width`` chars around the first word matching a term."""
    text = " ".join(text.split())
    if len(text) <= width:
        return text
    pos = 0
    for match in _WORD_RE.finditer(text):
        folded = fold(match.group())
        if any(folded.startswith(t) for t in terms):
            pos = match.start()
            break
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    if start > 0:
        space = text.find(" ", start)
        if 0 <= space < start + 20:
            start = space + 1
    if end < len(text):
        space = text.rfind(" ", start, end)
        if space > end - 20:
            end = space
    return ("… " if start > 0 else "") + text[start:end] + (" …" if end < len(text) else "")


def fmt_ts(seconds: float) -> str:
    """12.3 -> "00:12", 3723 -> "1:02:03"."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def similar(a: str, b: str, threshold: float = 0.8) -> bool:
    """Cheap near-duplicate check on folded word sets (Jaccard)."""
    wa, wb = set(words(fold(a))), set(words(fold(b)))
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def strip_reasoning(text: str) -> str:
    """Drop ``<think>`` blocks that reasoning models put before their answer."""
    return _THINK_RE.sub("", text).strip()


def extract_json(text: str) -> dict:
    """Parse the first JSON object in a model reply.

    Local models sometimes wrap JSON in code fences or add a sentence around it.
    Raises ``ValueError`` when no object can be decoded.
    """
    text = _FENCE_RE.sub("", strip_reasoning(text).strip()).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise ValueError("no JSON object in model reply") from None
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in model reply: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("model reply is not a JSON object")
    return value
