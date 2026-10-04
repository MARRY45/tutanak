"""SQLite storage for meetings, transcripts, slides, notes and the search index.

One connection is shared by the API threads and the recording workers, guarded by a
lock. That is plenty for a single-user local app and keeps writes strictly ordered.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .textutil import fold, fts_query

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    title_locked INTEGER NOT NULL DEFAULT 0,
    started_at   TEXT NOT NULL,
    duration     REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    language     TEXT,
    audio_file   TEXT,
    summary      TEXT,
    error        TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL,
    text       TEXT NOT NULL,
    speaker    TEXT
);
CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id, start_s);
CREATE TABLE IF NOT EXISTS slides (
    id         INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    t          REAL NOT NULL,
    image      TEXT NOT NULL,
    text       TEXT
);
CREATE INDEX IF NOT EXISTS slides_meeting ON slides(meeting_id, t);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL,
    body       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_meeting ON notes(meeting_id, start_s);
CREATE TABLE IF NOT EXISTS bookmarks (
    id         INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    t          REAL NOT NULL,
    label      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS passages (
    id         INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    start_s    REAL NOT NULL,
    end_s      REAL NOT NULL,
    text       TEXT NOT NULL,
    embedding  BLOB
);
CREATE INDEX IF NOT EXISTS passages_meeting ON passages(meeting_id);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(folded, tokenize='unicode61');
"""

_MEETING_FIELDS = {
    "title", "title_locked", "started_at", "duration", "status", "source",
    "language", "audio_file", "summary", "error",
}  # fmt: skip


def _segment(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "start": row["start_s"],
        "end": row["end_s"],
        "text": row["text"],
        "speaker": row["speaker"],
    }


def _meeting(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["title_locked"] = bool(data["title_locked"])
    data["summary"] = json.loads(data["summary"]) if data["summary"] else None
    return data


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- meetings

    def create_meeting(
        self,
        title: str,
        source: str,
        *,
        status: str = "recording",
        started_at: datetime | None = None,
        language: str | None = None,
        title_locked: bool = False,
    ) -> str:
        meeting_id = uuid.uuid4().hex[:12]
        started = (started_at or datetime.now()).isoformat(timespec="seconds")
        with self._lock:
            self._db.execute(
                "INSERT INTO meetings (id, title, title_locked, started_at, status, source, language)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (meeting_id, title, int(title_locked), started, status, source, language),
            )
        return meeting_id

    def update_meeting(self, meeting_id: str, **fields: Any) -> None:
        unknown = set(fields) - _MEETING_FIELDS
        if unknown:
            raise ValueError(f"unknown meeting fields: {sorted(unknown)}")
        if not fields:
            return
        if "summary" in fields and fields["summary"] is not None:
            fields["summary"] = json.dumps(fields["summary"], ensure_ascii=False)
        if "title_locked" in fields:
            fields["title_locked"] = int(fields["title_locked"])
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE meetings SET {assignments} WHERE id = ?", (*fields.values(), meeting_id)
            )

    def get_meeting(self, meeting_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return _meeting(row) if row else None

    def list_meetings(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT m.*,"
                " (SELECT COUNT(*) FROM segments s WHERE s.meeting_id = m.id) AS segment_count,"
                " (SELECT COUNT(*) FROM slides s WHERE s.meeting_id = m.id) AS slide_count"
                " FROM meetings m ORDER BY m.started_at DESC, m.rowid DESC"
            ).fetchall()
        return [_meeting(row) for row in rows]

    def meetings_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM meetings WHERE status IN ({marks})", statuses
            ).fetchall()
        return [_meeting(row) for row in rows]

    def delete_meeting(self, meeting_id: str) -> None:
        with self._lock:
            self._delete_passages(meeting_id)
            self._db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # ---------------------------------------------------------------- transcript

    def add_segment(
        self, meeting_id: str, start: float, end: float, text: str, speaker: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO segments (meeting_id, start_s, end_s, text, speaker) VALUES (?, ?, ?, ?, ?)",
                (meeting_id, start, end, text, speaker),
            )
        return {"id": cur.lastrowid, "start": start, "end": end, "text": text, "speaker": speaker}

    def segments(
        self, meeting_id: str, start: float | None = None, end: float | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM segments WHERE meeting_id = ?"
        args: list[Any] = [meeting_id]
        if start is not None:
            sql += " AND end_s > ?"
            args.append(start)
        if end is not None:
            sql += " AND start_s < ?"
            args.append(end)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY start_s, id", args).fetchall()
        return [_segment(row) for row in rows]

    def clear_segments(self, meeting_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))

    # ---------------------------------------------------------------- slides

    def add_slide(self, meeting_id: str, t: float, image: str, text: str | None = None) -> dict:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO slides (meeting_id, t, image, text) VALUES (?, ?, ?, ?)",
                (meeting_id, t, image, text),
            )
        return {"id": cur.lastrowid, "t": t, "image": image, "text": text}

    def set_slide_text(self, slide_id: int, text: str) -> None:
        with self._lock:
            self._db.execute("UPDATE slides SET text = ? WHERE id = ?", (text, slide_id))

    def slides(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, t, image, text FROM slides WHERE meeting_id = ? ORDER BY t, id",
                (meeting_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- notes

    def add_note(self, meeting_id: str, start: float, end: float, body: dict) -> dict[str, Any]:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO notes (meeting_id, start_s, end_s, body) VALUES (?, ?, ?, ?)",
                (meeting_id, start, end, json.dumps(body, ensure_ascii=False)),
            )
        return {"id": cur.lastrowid, "start": start, "end": end, **body}

    def notes(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM notes WHERE meeting_id = ? ORDER BY start_s, id", (meeting_id,)
            ).fetchall()
        return [
            {"id": r["id"], "start": r["start_s"], "end": r["end_s"], **json.loads(r["body"])}
            for r in rows
        ]

    def clear_notes(self, meeting_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM notes WHERE meeting_id = ?", (meeting_id,))

    # ---------------------------------------------------------------- bookmarks

    def add_bookmark(self, meeting_id: str, t: float, label: str = "") -> dict[str, Any]:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO bookmarks (meeting_id, t, label) VALUES (?, ?, ?)",
                (meeting_id, t, label),
            )
        return {"id": cur.lastrowid, "t": t, "label": label}

    def bookmarks(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, t, label FROM bookmarks WHERE meeting_id = ? ORDER BY t, id",
                (meeting_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_bookmark(self, meeting_id: str, bookmark_id: int) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM bookmarks WHERE id = ? AND meeting_id = ?", (bookmark_id, meeting_id)
            )

    # ---------------------------------------------------------------- search index

    def _delete_passages(self, meeting_id: str) -> None:
        self._db.execute(
            "DELETE FROM passages_fts WHERE rowid IN (SELECT id FROM passages WHERE meeting_id = ?)",
            (meeting_id,),
        )
        self._db.execute("DELETE FROM passages WHERE meeting_id = ?", (meeting_id,))

    def replace_passages(self, meeting_id: str, passages: list[dict[str, Any]]) -> list[int]:
        """Replace a meeting's search passages. Each needs kind, start, end, text."""
        ids: list[int] = []
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._delete_passages(meeting_id)
                for p in passages:
                    cur = self._db.execute(
                        "INSERT INTO passages (meeting_id, kind, start_s, end_s, text)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (meeting_id, p["kind"], p["start"], p["end"], p["text"]),
                    )
                    self._db.execute(
                        "INSERT INTO passages_fts (rowid, folded) VALUES (?, ?)",
                        (cur.lastrowid, fold(p["text"])),
                    )
                    ids.append(cur.lastrowid)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return ids

    def set_embeddings(self, ids: list[int], vectors: np.ndarray) -> None:
        rows = [
            (np.asarray(vec, dtype=np.float32).tobytes(), pid)
            for pid, vec in zip(ids, vectors, strict=True)
        ]
        with self._lock:
            self._db.executemany("UPDATE passages SET embedding = ? WHERE id = ?", rows)

    def embeddings(self, meeting_id: str | None = None) -> tuple[list[int], np.ndarray | None]:
        sql = "SELECT id, embedding FROM passages WHERE embedding IS NOT NULL"
        args: tuple = ()
        if meeting_id:
            sql += " AND meeting_id = ?"
            args = (meeting_id,)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        if not rows:
            return [], None
        dim = len(rows[0]["embedding"]) // 4
        usable = [r for r in rows if len(r["embedding"]) == dim * 4]
        matrix = np.frombuffer(b"".join(r["embedding"] for r in usable), dtype=np.float32)
        return [r["id"] for r in usable], matrix.reshape(len(usable), dim)

    def search_fts(
        self,
        terms: list[str],
        meeting_id: str | None = None,
        limit: int = 30,
        any_term: bool = False,
    ) -> list[int]:
        """Passage ids matching the folded terms, best match first."""
        if not terms:
            return []
        sql = (
            "SELECT p.id FROM passages_fts f JOIN passages p ON p.id = f.rowid"
            " WHERE passages_fts MATCH ?"
        )
        args: list[Any] = [fts_query(terms, any_term=any_term)]
        if meeting_id:
            sql += " AND p.meeting_id = ?"
            args.append(meeting_id)
        sql += " ORDER BY bm25(passages_fts) LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [row["id"] for row in rows]

    def passages_by_id(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        with self._lock:
            rows = self._db.execute(
                "SELECT p.id, p.meeting_id, p.kind, p.start_s, p.end_s, p.text,"
                " m.title AS meeting_title, m.started_at AS meeting_started_at"
                f" FROM passages p JOIN meetings m ON m.id = p.meeting_id WHERE p.id IN ({marks})",
                ids,
            ).fetchall()
        return {
            r["id"]: {
                "id": r["id"],
                "meeting_id": r["meeting_id"],
                "meeting_title": r["meeting_title"],
                "meeting_started_at": r["meeting_started_at"],
                "kind": r["kind"],
                "start": r["start_s"],
                "end": r["end_s"],
                "text": r["text"],
            }
            for r in rows
        }
