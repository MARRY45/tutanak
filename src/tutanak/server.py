"""Local web app and JSON API.

The server binds to 127.0.0.1 and additionally rejects requests whose ``Host`` is
not a loopback name (DNS rebinding) and state-changing requests coming from another
origin (a web page you happen to have open must not be able to start a recording).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import __version__
from .audio import capture as audio_capture
from .audio.capture import CaptureUnavailable
from .audio.files import ffmpeg_path
from .config import Config, Settings
from .export import to_markdown
from .importer import copy_stream, create_import, retranscribe, run_import, stage_upload
from .netguard import RemoteEndpointBlocked, assert_local, is_local_host_header
from .notes import catch_up, output_language
from .pipeline import Services, finalize, recover_interrupted
from .screen import capture as screen_capture
from .search import ask, retrieve
from .session import SessionError, SessionManager

WEB_DIR = Path(__file__).parent / "web"
_SLIDE_NAME = re.compile(r"^\d{4}\.jpg$")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_AUDIO_TYPES = {".m4a": "audio/mp4", ".wav": "audio/wav"}
_CSP = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class RecordingRequest(BaseModel):
    title: str | None = None
    microphone: str | None = "default"
    system: str | None = "default"
    screen: bool = False
    monitor: int = 1
    language: str | None = None


class BookmarkRequest(BaseModel):
    t: float | None = None
    label: str = Field("", max_length=200)


class CatchUpRequest(BaseModel):
    minutes: float = Field(5, gt=0, le=60)
    until: float | None = None


class TitleRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class RegenerateRequest(BaseModel):
    transcript: bool = False


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
    meeting_id: str | None = None


def create_app(services: Services | None = None, sessions: SessionManager | None = None) -> FastAPI:
    services = services or Services(Config())
    sessions = sessions or SessionManager(services)
    store = services.store

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        recover_interrupted(services)
        yield
        active = sessions.active()
        if active:
            active.stop()  # audio is flushed; the notes are finished on the next start

    app = FastAPI(title="Tutanak", version=__version__, lifespan=lifespan, docs_url="/api/docs")
    app.state.services = services
    app.state.sessions = sessions

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host")
        if not is_local_host_header(host):
            return JSONResponse({"detail": "Tutanak only answers local requests."}, status_code=403)
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != host:
                return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        # The interface may only load from, and talk to, this server.
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def meeting_or_404(meeting_id: str) -> dict[str, Any]:
        meeting = store.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(404, "meeting not found")
        return meeting

    def notes_language(meeting: dict[str, Any]) -> str:
        return output_language(services.settings.notes_language, meeting["language"])

    # ------------------------------------------------------------------ status

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        llm = services.llm.health()
        llm["error"] = services.llm_error
        ocr = services.ocr
        try:
            monitors = screen_capture.list_monitors()
        except Exception:
            monitors = []
        return {
            "version": __version__,
            "data_dir": str(services.cfg.home),
            "asr": services.transcriber.info(),
            "llm": llm,
            "ocr": {"engine": ocr.name if ocr else None},
            "ffmpeg": ffmpeg_path() is not None,
            "capture": {"audio": audio_capture.preload(), "screen": bool(monitors)},
        }

    @app.get("/api/devices")
    def devices() -> dict[str, Any]:
        try:
            found = audio_capture.list_devices()
            found["available"] = True
        except Exception as exc:
            found = {"available": False, "error": str(exc), "microphones": [], "speakers": []}
        try:
            found["monitors"] = screen_capture.list_monitors()
        except Exception:
            found["monitors"] = []
        found["asr"] = services.transcriber.info()
        return found

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return services.settings.model_dump()

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = Settings(**{**services.settings.model_dump(), **body})
            assert_local(settings.llm_base_url, allow_remote=settings.allow_remote_llm)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
        except RemoteEndpointBlocked as exc:
            raise HTTPException(400, str(exc)) from exc
        services.apply_settings(settings)
        return settings.model_dump()

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        queue = services.bus.subscribe()

        async def stream():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                services.bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ recording

    @app.post("/api/recordings", status_code=201)
    def start_recording(body: RecordingRequest) -> dict[str, Any]:
        try:
            session = sessions.start(**body.model_dump())
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except CaptureUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return session.state()

    @app.post("/api/recordings/stop")
    def stop_recording() -> dict[str, Any]:
        try:
            session = sessions.stop()
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": session.meeting_id}

    @app.get("/api/recordings/current")
    def current_recording() -> dict[str, Any] | None:
        session = sessions.active()
        return session.state() if session else None

    # ------------------------------------------------------------------ meetings

    @app.get("/api/meetings")
    def list_meetings() -> list[dict[str, Any]]:
        out = []
        for meeting in store.list_meetings():
            summary = meeting.pop("summary") or {}
            meeting["tldr"] = summary.get("tldr")
            meeting["decision_count"] = len(summary.get("decisions", []))
            meeting["action_count"] = len(summary.get("actions", []))
            meeting["progress"] = services.progress.get(meeting["id"])
            out.append(meeting)
        return out

    @app.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: str) -> dict[str, Any]:
        meeting = meeting_or_404(meeting_id)
        session = sessions.active()
        return {
            "meeting": meeting,
            "segments": store.segments(meeting_id),
            "slides": store.slides(meeting_id),
            "notes": store.notes(meeting_id),
            "bookmarks": store.bookmarks(meeting_id),
            "progress": services.progress.get(meeting_id),
            "live": session.state() if session and session.meeting_id == meeting_id else None,
        }

    @app.patch("/api/meetings/{meeting_id}")
    def rename_meeting(meeting_id: str, body: TitleRequest) -> dict[str, Any]:
        meeting_or_404(meeting_id)
        store.update_meeting(meeting_id, title=body.title.strip(), title_locked=True)
        return store.get_meeting(meeting_id)

    @app.delete("/api/meetings/{meeting_id}", status_code=204)
    def delete_meeting(meeting_id: str) -> Response:
        meeting = meeting_or_404(meeting_id)
        session = sessions.active()
        if (session and session.meeting_id == meeting_id) or meeting["status"] == "processing":
            raise HTTPException(409, "this meeting is still being recorded or processed")
        store.delete_meeting(meeting_id)
        shutil.rmtree(services.cfg.meetings_dir / meeting_id, ignore_errors=True)
        return Response(status_code=204)

    @app.get("/api/meetings/{meeting_id}/audio")
    def meeting_audio(meeting_id: str) -> FileResponse:
        meeting = meeting_or_404(meeting_id)
        if not meeting["audio_file"]:
            raise HTTPException(404, "this meeting has no audio")
        path = services.cfg.meetings_dir / meeting_id / Path(meeting["audio_file"]).name
        if not path.is_file():
            raise HTTPException(404, "audio file is missing")
        return FileResponse(
            path, media_type=_AUDIO_TYPES.get(path.suffix, "application/octet-stream")
        )

    @app.get("/api/meetings/{meeting_id}/slides/{name}")
    def meeting_slide(meeting_id: str, name: str) -> FileResponse:
        meeting_or_404(meeting_id)
        if not _SLIDE_NAME.match(name):
            raise HTTPException(404, "slide not found")
        path = services.cfg.meetings_dir / meeting_id / "slides" / name
        if not path.is_file():
            raise HTTPException(404, "slide not found")
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
        )

    @app.get("/api/meetings/{meeting_id}/export.md")
    def export_meeting(meeting_id: str) -> Response:
        meeting = meeting_or_404(meeting_id)
        text = to_markdown(store, meeting_id, language=services.settings.ui_language) or ""
        filename = (
            re.sub(r"[^\w\-]+", "-", meeting["title"], flags=re.UNICODE).strip("-") or "meeting"
        )
        ascii_name = filename.encode("ascii", "ignore").decode() or "meeting"
        return Response(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{ascii_name}.md"'},
        )

    @app.post("/api/meetings/{meeting_id}/catchup")
    def meeting_catch_up(meeting_id: str, body: CatchUpRequest) -> dict[str, Any]:
        meeting = meeting_or_404(meeting_id)
        session = sessions.active()
        if body.until is not None:
            until = body.until
        elif session and session.meeting_id == meeting_id:
            until = session.elapsed
        else:
            until = meeting["duration"]
        return catch_up(
            store,
            services.llm,
            meeting_id,
            until=until,
            minutes=body.minutes,
            language=notes_language(meeting),
        )

    @app.post("/api/meetings/{meeting_id}/bookmarks", status_code=201)
    def add_bookmark(meeting_id: str, body: BookmarkRequest) -> dict[str, Any]:
        meeting_or_404(meeting_id)
        session = sessions.active()
        if body.t is None:
            if not session or session.meeting_id != meeting_id:
                raise HTTPException(422, "t is required for a meeting that is not being recorded")
            return session.bookmark(body.label.strip())
        return store.add_bookmark(meeting_id, max(0.0, body.t), body.label.strip())

    @app.delete("/api/meetings/{meeting_id}/bookmarks/{bookmark_id}", status_code=204)
    def delete_bookmark(meeting_id: str, bookmark_id: int) -> Response:
        meeting_or_404(meeting_id)
        store.delete_bookmark(meeting_id, bookmark_id)
        return Response(status_code=204)

    @app.post("/api/meetings/{meeting_id}/regenerate", status_code=202)
    def regenerate(meeting_id: str, body: RegenerateRequest) -> dict[str, Any]:
        meeting = meeting_or_404(meeting_id)
        session = sessions.active()
        if (session and session.meeting_id == meeting_id) or meeting["status"] == "processing":
            raise HTTPException(409, "this meeting is still being recorded or processed")
        if body.transcript and not meeting["audio_file"]:
            raise HTTPException(409, "this meeting has no audio to transcribe")
        store.update_meeting(meeting_id, status="processing", error=None)
        if body.transcript:
            services.jobs.submit(retranscribe, services, meeting_id)
        else:
            store.clear_notes(meeting_id)
            store.update_meeting(meeting_id, summary=None)
            services.jobs.submit(finalize, services, meeting_id)
        return {"id": meeting_id, "status": "processing"}

    @app.post("/api/import", status_code=202)
    def import_recording(
        file: UploadFile = File(...),
        title: str | None = Form(None),
        last_modified: float | None = Form(None),
        language: str | None = Form(None),
    ) -> dict[str, Any]:
        if ffmpeg_path() is None:
            raise HTTPException(
                503, "ffmpeg is required to import recordings and was not found on PATH"
            )
        name = Path(file.filename or "recording").name
        staged = stage_upload(services, name)
        copy_stream(file.file, staged)
        recorded_at = None
        if last_modified:
            try:
                recorded_at = datetime.fromtimestamp(last_modified / 1000)
            except (OverflowError, OSError, ValueError):
                recorded_at = None
        meeting_id = create_import(
            services, Path(name), title=title or None, recorded_at=recorded_at
        )
        services.set_progress(meeting_id, "queued")
        services.jobs.submit(
            run_import, services, meeting_id, staged, language=language or None, delete_source=True
        )
        return {"id": meeting_id}

    # ------------------------------------------------------------------ search

    @app.get("/api/search")
    def search(q: str, meeting_id: str | None = None, limit: int = 30) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"query": query, "hits": []}
        hits = retrieve(store, services.llm, query, meeting_id, limit=max(1, min(limit, 100)))
        return {"query": query, "hits": hits}

    @app.post("/api/ask")
    def ask_meetings(body: AskRequest) -> dict[str, Any]:
        if body.meeting_id:
            meeting_or_404(body.meeting_id)
        return ask(store, services.llm, body.question.strip(), body.meeting_id)

    # ------------------------------------------------------------------ web app

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
