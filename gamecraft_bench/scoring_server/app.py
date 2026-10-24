from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Security, UploadFile, status,
)
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..verifier.judges import MultimodalJudge
from .evidence import (
    EvidenceError, demo_id_for_filename, probe_video, save_upload,
    validate_rubric, write_manifest,
)
from .settings import Settings
from .store import EvaluationStore
from .worker import EvaluationWorker


def create_app(
    settings: Settings | None = None,
    judge: MultimodalJudge | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = EvaluationStore(settings.data_dir / "evaluations.sqlite3")
    worker = EvaluationWorker(settings, store, judge)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.start()
        try:
            yield
        finally:
            await worker.stop()

    app = FastAPI(title="GameCraft evaluation server", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.worker = worker

    async def authorized(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        if settings.api_token is None:
            return
        if (credentials is None or credentials.scheme.lower() != "bearer"
                or not secrets.compare_digest(credentials.credentials, settings.api_token)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.exception_handler(Exception)
    async def unhandled_error(_, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/evaluations",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(authorized)],
    )
    async def submit_evaluation(
        rubric: UploadFile = File(...),
        videos: list[UploadFile] = File(...),
        build_ok: bool = Form(True),
    ) -> dict[str, str]:
        if not 1 <= len(videos) <= min(10, settings.max_videos):
            for video in videos:
                await video.close()
            await rubric.close()
            raise HTTPException(status_code=400, detail="provide between 1 and 10 videos")
        try:
            demo_ids = [demo_id_for_filename(video.filename) for video in videos]
        except EvidenceError as exc:
            for video in videos:
                await video.close()
            await rubric.close()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if len(demo_ids) != len(set(demo_ids)):
            for video in videos:
                await video.close()
            await rubric.close()
            raise HTTPException(status_code=400, detail="demo IDs must be unique")

        evaluation_id = uuid.uuid4().hex
        root = settings.data_dir / evaluation_id
        root.mkdir()
        remaining = [settings.max_total_bytes]
        try:
            await save_upload(
                rubric, root / "rubric.json",
                limit=settings.max_rubric_bytes, remaining=remaining, label="rubric",
            )
            validate_rubric(root / "rubric.json", settings)
            manifest = []
            for demo_id, video in zip(demo_ids, videos, strict=True):
                await save_upload(
                    video, root / "videos" / f"{demo_id}.mp4",
                    limit=settings.max_video_bytes, remaining=remaining, label=demo_id,
                )
                duration = await asyncio.to_thread(
                    probe_video, root / "videos" / f"{demo_id}.mp4", settings
                )
                manifest.append((demo_id, duration))
            write_manifest(root, manifest)
            (root / "build_ok").write_text("true" if build_ok else "false", encoding="ascii")
            store.create(evaluation_id)
            worker.enqueue(evaluation_id)
        except EvidenceError as exc:
            shutil.rmtree(root, ignore_errors=True)
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return {"evaluation_id": evaluation_id, "status": "queued"}

    def get_evaluation(evaluation_id: str) -> dict:
        record = store.get(evaluation_id)
        if record is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return record

    @app.get("/v1/evaluations/{evaluation_id}", dependencies=[Depends(authorized)])
    async def evaluation_status(evaluation_id: str) -> dict:
        return get_evaluation(evaluation_id)

    @app.get("/v1/evaluations/{evaluation_id}/result", dependencies=[Depends(authorized)])
    async def evaluation_result(evaluation_id: str) -> dict:
        record = get_evaluation(evaluation_id)
        if record["status"] != "succeeded":
            raise HTTPException(
                status_code=409,
                detail={"status": record["status"], "error": record["error"]},
            )
        result_path = settings.data_dir / evaluation_id / "output" / "breakdown.json"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail="result is unavailable") from exc
        result = _public_result(result, str(settings.data_dir))
        result.update({
            "evaluation_id": evaluation_id,
            "status": "succeeded",
            "trust": "unverified",
        })
        return result

    return app


def _public_result(value, data_dir: str):
    if isinstance(value, dict):
        return {
            key: _public_result(item, data_dir)
            for key, item in value.items()
            if key not in {
                "mp4", "mp4_path", "frames", "frame_paths", "video_path",
                "output_dir", "build_log",
            } and not (key in {"trace", "trace_path"} and item is not None)
        }
    if isinstance(value, list):
        return [_public_result(item, data_dir) for item in value]
    if isinstance(value, str):
        return value.replace(data_dir, "[server]")
    return value
