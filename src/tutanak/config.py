"""Settings and data locations.

Everything Tutanak stores lives under one directory (``TUTANAK_HOME``, default
``~/.tutanak``): the SQLite database, audio, slide images and ``settings.json``.
Deleting that directory deletes every trace of every meeting.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ENV_HOME = "TUTANAK_HOME"


def default_home() -> Path:
    env = os.environ.get(ENV_HOME)
    return Path(env).expanduser() if env else Path.home() / ".tutanak"


class Settings(BaseModel):
    """User-editable settings, persisted as ``settings.json`` in the data directory."""

    # Speech-to-text
    language: str = Field("auto", description="Spoken language (ISO 639-1) or 'auto'.")
    whisper_model: str = Field("auto", description="faster-whisper model name or 'auto'.")
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"

    # Local LLM (any OpenAI-compatible server: Ollama, LM Studio, llama.cpp)
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "gemma3:4b"
    embed_model: str = Field(
        "bge-m3", description="Embedding model; empty disables semantic search."
    )
    allow_remote_llm: bool = Field(
        False, description="Allow an LLM endpoint that is not on this machine."
    )
    llm_timeout_s: float = 180.0

    # Notes
    notes_language: str = Field("auto", description="'auto' follows the spoken language.")
    note_interval_s: int = Field(180, ge=30, le=900)

    # Screen / slides
    screen_interval_s: float = Field(2.0, ge=0.5, le=30)
    ocr_engine: Literal["auto", "windows", "rapidocr", "off"] = "auto"

    ui_language: Literal["tr", "en"] = "tr"


class Config:
    """Resolved paths plus the current settings."""

    def __init__(self, home: Path | None = None, settings: Settings | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.meetings_dir.mkdir(exist_ok=True)
        self.settings = settings or self._load()

    @property
    def db_path(self) -> Path:
        return self.home / "tutanak.db"

    @property
    def meetings_dir(self) -> Path:
        return self.home / "meetings"

    @property
    def settings_path(self) -> Path:
        return self.home / "settings.json"

    def meeting_dir(self, meeting_id: str) -> Path:
        path = self.meetings_dir / meeting_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load(self) -> Settings:
        data: dict = {}
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        # Environment overrides, e.g. TUTANAK_LLM_MODEL=qwen3:8b
        for name in Settings.model_fields:
            env = os.environ.get(f"TUTANAK_{name.upper()}")
            if env is not None:
                data[name] = env
        try:
            return Settings(**data)
        except ValueError:
            return Settings()

    def save(self, settings: Settings) -> None:
        self.settings = settings
        self.settings_path.write_text(
            json.dumps(settings.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
