"""Markdown export of a meeting (plain files work well with Obsidian and git)."""

from __future__ import annotations

from .store import Store
from .textutil import fmt_ts

_LABELS = {
    "tr": {
        "date": "Tarih", "duration": "Süre", "summary": "Özet", "key_points": "Öne çıkanlar",
        "decisions": "Kararlar", "actions": "Yapılacaklar", "questions": "Açık sorular",
        "notes": "Notlar", "slides": "Slaytlar", "slide": "Slayt", "transcript": "Transkript",
        "bookmarks": "İşaretler", "me": "Ben", "others": "Diğerleri", "owner": "sorumlu",
        "due": "tarih",
    },
    "en": {
        "date": "Date", "duration": "Duration", "summary": "Summary", "key_points": "Key points",
        "decisions": "Decisions", "actions": "Action items", "questions": "Open questions",
        "notes": "Notes", "slides": "Slides", "slide": "Slide", "transcript": "Transcript",
        "bookmarks": "Bookmarks", "me": "Me", "others": "Others", "owner": "owner",
        "due": "due",
    },
}  # fmt: skip


def _stamp(t: float | None) -> str:
    return f"`{fmt_ts(t)}` " if t is not None else ""


def _action(item: dict, labels: dict) -> str:
    extra = []
    if item.get("owner"):
        extra.append(f"{labels['owner']}: {item['owner']}")
    if item.get("due"):
        extra.append(f"{labels['due']}: {item['due']}")
    suffix = f" ({', '.join(extra)})" if extra else ""
    return f"- [ ] {_stamp(item.get('t'))}{item['task']}{suffix}"


def to_markdown(store: Store, meeting_id: str, *, language: str = "tr") -> str | None:
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        return None
    labels = _LABELS.get(language, _LABELS["en"])
    out: list[str] = [f"# {meeting['title']}", ""]
    out.append(
        f"- **{labels['date']}:** {meeting['started_at'].replace('T', ' ')[:16]}\n"
        f"- **{labels['duration']}:** {fmt_ts(meeting['duration'])}"
    )

    summary = meeting["summary"]
    if summary:
        out += ["", f"## {labels['summary']}", "", summary.get("tldr", "")]
        if summary.get("key_points"):
            out += ["", f"### {labels['key_points']}", ""]
            out += [f"- {point}" for point in summary["key_points"]]
        if summary.get("decisions"):
            out += ["", f"### {labels['decisions']}", ""]
            out += [f"- {_stamp(d.get('t'))}{d['text']}" for d in summary["decisions"]]
        if summary.get("actions"):
            out += ["", f"### {labels['actions']}", ""]
            out += [_action(a, labels) for a in summary["actions"]]
        if summary.get("questions"):
            out += ["", f"### {labels['questions']}", ""]
            out += [f"- {_stamp(q.get('t'))}{q['text']}" for q in summary["questions"]]

    bookmarks = store.bookmarks(meeting_id)
    if bookmarks:
        out += ["", f"## {labels['bookmarks']}", ""]
        out += [f"- {_stamp(b['t'])}{b['label']}".rstrip() for b in bookmarks]

    notes = store.notes(meeting_id)
    if notes:
        out += ["", f"## {labels['notes']}"]
        for note in notes:
            out += ["", f"### {fmt_ts(note['start'])} · {note['topic']}", ""]
            out += [f"- {p['text']}" for p in note.get("points", [])]

    slides = store.slides(meeting_id)
    if slides:
        out += ["", f"## {labels['slides']}"]
        for number, slide in enumerate(slides, 1):
            out += ["", f"### {fmt_ts(slide['t'])} · {labels['slide']} {number}", ""]
            out.append(f"![{labels['slide']} {number}](slides/{slide['image']})")
            text = (slide.get("text") or "").strip()
            if text:
                out += ["", *[f"> {line}" for line in text.splitlines() if line.strip()]]

    segments = store.segments(meeting_id)
    if segments:
        out += ["", f"## {labels['transcript']}", ""]
        for seg in segments:
            who = labels.get(seg["speaker"] or "", "")
            prefix = f"**{who}:** " if who else ""
            out.append(f"`{fmt_ts(seg['start'])}` {prefix}{seg['text']}  ")
    return "\n".join(out).rstrip() + "\n"
