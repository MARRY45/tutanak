"""Shapes the local model must reply in.

Small local models are sloppy: they return a bare string where an object was asked
for, or ``"ref": "[12]"`` instead of ``12``. The validators accept those variants so
a usable answer is not thrown away over formatting.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_INT_RE = re.compile(r"\d+")


def _first_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, list):
        return _first_int(value[0]) if value else None
    match = _INT_RE.search(str(value))
    return int(match.group()) if match else None


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "n/a", "-", "yok"}:
        return None
    return value


class Point(BaseModel):
    text: str
    ref: int | None = Field(None, description="Number of the transcript line this is based on.")

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        return {"text": value} if isinstance(value, str) else value

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> int | None:
        return _first_int(value)


class Action(BaseModel):
    task: str
    owner: str | None = None
    due: str | None = None
    ref: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"task": value}
        if isinstance(value, dict) and "task" not in value and "text" in value:
            return {**value, "task": value["text"]}
        return value

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> int | None:
        return _first_int(value)

    @field_validator("owner", "due", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> Any:
        value = _blank_to_none(value)
        return value if value is None else str(value)


class BlockNotes(BaseModel):
    """Notes for one stretch of the meeting (a few minutes)."""

    topic: str
    points: list[Point] = Field(default_factory=list)
    decisions: list[Point] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    questions: list[Point] = Field(default_factory=list)

    @field_validator("points", "decisions", "actions", "questions", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


class Overview(BaseModel):
    """Meeting-level summary written from the block notes."""

    title: str
    tldr: str
    key_points: list[str] = Field(default_factory=list)

    @field_validator("key_points", mode="before")
    @classmethod
    def _strings(cls, value: Any) -> Any:
        if value is None:
            return []
        return [v.get("text", "") if isinstance(v, dict) else v for v in value]


class CatchUp(BaseModel):
    """What happened in the last few minutes."""

    headline: str
    points: list[Point] = Field(default_factory=list)

    @field_validator("points", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value
