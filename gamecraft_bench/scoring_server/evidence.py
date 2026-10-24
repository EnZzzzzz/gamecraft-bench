from __future__ import annotations

import ast
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import UploadFile

from ..verifier.score import DemoArtifacts, _sample_frames
from .settings import Settings

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)
_CHUNK_SIZE = 1024 * 1024


class EvidenceError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def demo_id_for_filename(filename: str | None) -> str:
    if not filename or filename in {".", ".."}:
        raise EvidenceError("each video must have an MP4 filename")
    if Path(filename).name != filename or "\\" in filename:
        raise EvidenceError("video filenames must not contain paths")
    path = Path(filename)
    if path.suffix.lower() != ".mp4":
        raise EvidenceError("videos must use the .mp4 extension")
    demo_id = path.stem
    if not _SAFE_ID.fullmatch(demo_id):
        raise EvidenceError(
            "demo IDs must be 1-64 ASCII letters, digits, underscores, or hyphens"
        )
    return demo_id


async def save_upload(
    upload: UploadFile,
    destination: Path,
    *,
    limit: int,
    remaining: list[int],
    label: str,
) -> int:
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(_CHUNK_SIZE):
                size += len(chunk)
                remaining[0] -= len(chunk)
                if size > limit:
                    raise EvidenceError(f"{label} exceeds its size limit", status_code=413)
                if remaining[0] < 0:
                    raise EvidenceError("upload exceeds the total size limit", status_code=413)
                output.write(chunk)
    finally:
        await upload.close()
    if size == 0:
        raise EvidenceError(f"{label} must not be empty")
    return size


def validate_rubric(path: Path, settings: Settings) -> dict:
    try:
        rubric = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("rubric must be valid UTF-8 JSON") from exc
    if not isinstance(rubric, dict):
        raise EvidenceError("rubric must be a JSON object")

    requirements = rubric.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        raise EvidenceError("rubric requirements must be a non-empty array")
    if len(requirements) > settings.max_requirements:
        raise EvidenceError("rubric exceeds the requirement count limit")
    build_check = rubric.get("build_check")
    if not isinstance(build_check, dict):
        raise EvidenceError("build_check must be an object")
    build_id = build_check.get("id", "BUILD")
    ids = [_validate_id(build_id, "build_check ID")]
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise EvidenceError("each requirement must be an object")
        ids.append(_validate_id(requirement.get("id"), "requirement ID"))
        description = requirement.get("description")
        if (not isinstance(description, str) or not description.strip()
                or len(description) > settings.max_description_length):
            raise EvidenceError("requirement descriptions must be non-empty and bounded")
        if requirement.get("agg", "max") not in {"max", "mean"}:
            raise EvidenceError("requirement agg must be 'max' or 'mean'")
    if len(ids) != len(set(ids)):
        raise EvidenceError("requirement and build IDs must be unique")

    formula = rubric.get("score_formula")
    if not isinstance(formula, str) or not formula.strip():
        raise EvidenceError("score_formula must be a non-empty string")
    if len(formula) > settings.max_formula_length:
        raise EvidenceError("score_formula exceeds its length limit")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise EvidenceError("score_formula is not a valid expression") from exc
    if sum(1 for _ in ast.walk(tree)) > settings.max_formula_nodes:
        raise EvidenceError("score_formula exceeds its AST node limit")
    allowed_binary = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
    for node in ast.walk(tree):
        if isinstance(node, ast.Pow):
            raise EvidenceError("score_formula must not use exponentiation")
        if isinstance(node, ast.BinOp) and not isinstance(node.op, allowed_binary):
            message = ("score_formula must not use exponentiation"
                       if isinstance(node.op, ast.Pow)
                       else "score_formula contains a disallowed operator")
            raise EvidenceError(message)
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise EvidenceError("score_formula contains a disallowed operator")
        if isinstance(node, ast.Name) and node.id not in ids:
            raise EvidenceError("score_formula references an unknown ID")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise EvidenceError("score_formula constants must be numeric")
            if not math.isfinite(float(node.value)):
                raise EvidenceError("score_formula constants must be finite")
        if not isinstance(node, (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load,
            ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
            ast.Mod, ast.UAdd, ast.USub,
        )):
            raise EvidenceError("score_formula contains a disallowed expression")
    return rubric


def _validate_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise EvidenceError(f"{label} is missing or unsafe")
    return value


def probe_video(path: Path, settings: Settings) -> float:
    if shutil.which("ffprobe"):
        command = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type:format=duration",
            "-of", "json", str(path),
        ]
        process = _run_media_command(command, settings)
        try:
            metadata = json.loads(process.stdout)
            streams = metadata["streams"]
            duration = float(metadata["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvidenceError("video metadata is invalid") from exc
        if not any(stream.get("codec_type") == "video" for stream in streams):
            raise EvidenceError("upload does not contain a video stream")
    else:
        duration = _probe_with_ffmpeg(path, settings)
    if not math.isfinite(duration) or duration <= 0:
        raise EvidenceError("video duration must be finite and positive")
    if duration > settings.max_demo_seconds:
        raise EvidenceError("video exceeds the configured duration limit")
    return duration


def _run_media_command(command: list[str], settings: Settings) -> subprocess.CompletedProcess:
    try:
        process = subprocess.run(
            command, capture_output=True, text=True,
            timeout=settings.command_timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("video could not be inspected") from exc
    if process.returncode != 0:
        raise EvidenceError("video is not a valid MP4 recording")
    return process


def _probe_with_ffmpeg(path: Path, settings: Settings) -> float:
    process = _run_media_command([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
        "-f", "null", "-", "-progress", "pipe:1", "-nostats",
    ], settings)
    lines = process.stdout.splitlines()
    if "progress=end" not in lines:
        raise EvidenceError("video metadata is invalid")
    timestamps = [
        float(line.split("=", 1)[1]) / 1_000_000
        for line in lines if line.startswith("out_time_us=")
    ]
    if not timestamps:
        raise EvidenceError("video duration is unavailable")
    return max(timestamps)


def write_manifest(root: Path, demos: list[tuple[str, float]]) -> None:
    (root / "manifest.json").write_text(
        json.dumps([{"demo_id": demo_id, "duration_seconds": duration}
                    for demo_id, duration in demos]),
        encoding="utf-8",
    )


def build_demo_artifacts(root: Path) -> list[DemoArtifacts]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    artifacts = []
    for entry in manifest:
        demo_id = entry["demo_id"]
        mp4_path = root / "videos" / f"{demo_id}.mp4"
        duration = float(entry["duration_seconds"])
        frames = _sample_frames(
            mp4_path,
            root / "output" / "demos" / demo_id / "frames",
            duration_seconds=duration,
            interval_seconds=0.5,
        )
        if not frames:
            raise EvidenceError(f"could not extract frames for {demo_id}")
        artifacts.append(DemoArtifacts(
            demo_id=demo_id,
            trace_path=None,
            mp4_path=mp4_path,
            frame_paths=frames,
            duration_seconds=duration,
        ))
    return artifacts
