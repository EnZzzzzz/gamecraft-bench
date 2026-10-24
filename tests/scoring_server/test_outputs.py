from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gamecraft_bench.scoring_server import Settings, create_app
from gamecraft_bench.verifier.judges.base import JudgeResponse
from gamecraft_bench.verifier.judges.stub import StubJudge


def rubric() -> dict:
    return {
        "score_formula": "BUILD * (0.6 * BEST + 0.4 * AVERAGE)",
        "build_check": {"id": "BUILD", "cmd": "must not be executed"},
        "runtime": {"type": "web", "start_cmd": "must not be executed"},
        "requirements": [
            {"id": "BEST", "description": "best demo", "agg": "max"},
            {"id": "AVERAGE", "description": "all demos", "agg": "mean"},
        ],
    }


def multipart(rubric_value: dict | str | None = None, names=("first",)) -> list:
    rubric_data = rubric_value if isinstance(rubric_value, str) else json.dumps(
        rubric_value if rubric_value is not None else rubric()
    )
    return [
        ("rubric", ("rubric.json", rubric_data, "application/json")),
        *(("videos", (name + ".mp4", b"not a real video", "video/mp4"))
          for name in names),
    ]


def require_video_tools() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")


def make_video(path: Path, *, seconds: float = 1.0) -> bytes:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"testsrc=size=64x64:rate=10:duration={seconds}",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=30)
    return path.read_bytes()


def valid_multipart(rubric_value: dict, videos: dict[str, bytes]) -> list:
    return [
        ("rubric", ("rubric.json", json.dumps(rubric_value), "application/json")),
        *(("videos", (f"{name}.mp4", data, "video/mp4"))
          for name, data in videos.items()),
    ]


def wait_for_status(client: TestClient, evaluation_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/v1/evaluations/{evaluation_id}")
        assert response.status_code == 200
        record = response.json()
        if record["status"] == expected:
            return record
        assert record["status"] != "failed" or expected == "failed", record
        time.sleep(0.02)
    pytest.fail(f"evaluation did not reach {expected}: {record}")


class RecordingStubJudge(StubJudge):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []

    def score(self, request):
        self.requests.append(request)
        first = request.demo_id == "first"
        return JudgeResponse(scores={
            "BEST": 1.0 if first else 0.0,
            "AVERAGE": 1.0 if first else 0.0,
        })


def test_two_videos_complete_flow_and_public_result(tmp_path: Path) -> None:
    require_video_tools()
    first = make_video(tmp_path / "first-source.mp4")
    second = make_video(tmp_path / "second-source.mp4")
    judge = RecordingStubJudge()
    marker = tmp_path / "command-executed"
    spec = rubric()
    spec["build_check"]["cmd"] = f"touch {marker}"
    spec["runtime"]["start_cmd"] = f"touch {marker}"
    app = create_app(Settings(data_dir=tmp_path / "evaluations"), judge=judge)

    with TestClient(app) as client:
        response = client.post(
            "/v1/evaluations",
            files=valid_multipart(spec, {"first": first, "second": second}),
        )
        assert response.status_code == 202, response.text
        evaluation_id = response.json()["evaluation_id"]
        assert response.json()["status"] == "queued"
        pending = client.get(f"/v1/evaluations/{evaluation_id}/result")
        assert pending.status_code in {200, 409}
        record = wait_for_status(client, evaluation_id, "succeeded")
        assert record["created_at"] and record["started_at"] and record["finished_at"]
        assert record["error"] is None

        result_response = client.get(f"/v1/evaluations/{evaluation_id}/result")
        assert result_response.status_code == 200
        result = result_response.json()
        assert result["reward"] == pytest.approx(0.8)
        assert result["trust"] == "unverified"
        assert result["evaluation_id"] == evaluation_id
        assert [demo["demo_id"] for demo in result["demos"]] == ["first", "second"]
        assert all(set(demo) == {"demo_id", "trace", "duration_seconds"}
                   for demo in result["demos"])
        assert all(demo["trace"] is None for demo in result["demos"])
        requirements = {item["id"]: item for item in result["requirements"]}
        assert requirements["BEST"]["aggregated"] == 1.0
        assert requirements["AVERAGE"]["aggregated"] == 0.5
        assert str(tmp_path) not in result_response.text

        root = tmp_path / "evaluations" / evaluation_id / "output"
        breakdown = json.loads((root / "breakdown.json").read_text())
        assert all(demo["trace"] is None for demo in breakdown["demos"])
        assert (root / "judge_log.json").is_file()

    assert len(judge.requests) == 2
    assert all(request.frame_paths for request in judge.requests)
    assert not marker.exists()


def test_invalid_uploads_and_rubrics(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "evaluations"), judge=StubJudge())
    with TestClient(app) as client:
        assert client.post("/v1/evaluations", files=multipart(names=("bad.txt",))).status_code == 400
        assert client.post("/v1/evaluations", files=multipart(names=("../escape",))).status_code == 400
        assert client.post("/v1/evaluations", files=multipart(names=("same", "same"))).status_code == 400
        assert client.post(
            "/v1/evaluations", files=multipart(names=tuple(f"demo{i}" for i in range(11)))
        ).status_code == 400
        assert client.post("/v1/evaluations", files=multipart("not json")).status_code == 400

        duplicate = rubric()
        duplicate["requirements"][1]["id"] = "BEST"
        assert client.post("/v1/evaluations", files=multipart(duplicate)).status_code == 400
        missing_description = rubric()
        del missing_description["requirements"][0]["description"]
        assert client.post("/v1/evaluations", files=multipart(missing_description)).status_code == 400
        power = rubric()
        power["score_formula"] = "BEST ** 2"
        response = client.post("/v1/evaluations", files=multipart(power))
        assert response.status_code == 400
        assert "exponentiation" in response.text
        huge = rubric()
        huge["score_formula"] = "+".join(["BEST"] * 200)
        assert client.post("/v1/evaluations", files=multipart(huge)).status_code == 400


def test_size_duration_and_real_video_validation(tmp_path: Path) -> None:
    require_video_tools()
    video = make_video(tmp_path / "source.mp4")
    small = create_app(Settings(
        data_dir=tmp_path / "small", max_rubric_bytes=10,
    ), judge=StubJudge())
    with TestClient(small) as client:
        assert client.post(
            "/v1/evaluations", files=valid_multipart(rubric(), {"first": video})
        ).status_code == 413

    short_limit = create_app(Settings(
        data_dir=tmp_path / "duration", max_demo_seconds=0.1,
    ), judge=StubJudge())
    with TestClient(short_limit) as client:
        response = client.post(
            "/v1/evaluations", files=valid_multipart(rubric(), {"first": video})
        )
        assert response.status_code == 400
        assert "duration" in response.text

    invalid = create_app(Settings(data_dir=tmp_path / "invalid"), judge=StubJudge())
    with TestClient(invalid) as client:
        assert client.post(
            "/v1/evaluations", files=valid_multipart(rubric(), {"first": b"plain text"})
        ).status_code == 400


def test_not_found_conflict_and_token(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "evaluations", api_token="secret"))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/evaluations/missing").status_code == 401
        headers = {"Authorization": "Bearer secret"}
        assert client.get("/v1/evaluations/missing", headers=headers).status_code == 404
        assert client.get("/v1/evaluations/missing/result", headers=headers).status_code == 404
        assert client.post(
            "/v1/evaluations", files=multipart(), headers=headers
        ).status_code == 400


class BlockingJudge(StubJudge):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.started = threading.Event()

    def score(self, request):
        self.started.set()
        assert self.release.wait(5)
        return super().score(request)


def test_unfinished_result_conflict_and_safe_failure(tmp_path: Path) -> None:
    require_video_tools()
    video = make_video(tmp_path / "source.mp4")
    judge = BlockingJudge()
    app = create_app(Settings(data_dir=tmp_path / "evaluations"), judge=judge)
    with TestClient(app) as client:
        response = client.post(
            "/v1/evaluations", files=valid_multipart(rubric(), {"first": video})
        )
        evaluation_id = response.json()["evaluation_id"]
        assert judge.started.wait(5)
        conflict = client.get(f"/v1/evaluations/{evaluation_id}/result")
        assert conflict.status_code == 409
        judge.release.set()
        wait_for_status(client, evaluation_id, "succeeded")

    class FailingJudge(StubJudge):
        def score(self, request):
            raise RuntimeError(f"private path: {tmp_path}")

    failing = create_app(Settings(data_dir=tmp_path / "failed"), judge=FailingJudge())
    with TestClient(failing) as client:
        response = client.post(
            "/v1/evaluations", files=valid_multipart(rubric(), {"first": video})
        )
        evaluation_id = response.json()["evaluation_id"]
        record = wait_for_status(client, evaluation_id, "failed")
        assert "evaluation failed" in record["error"]
        assert str(tmp_path) not in record["error"]
        assert client.get(f"/v1/evaluations/{evaluation_id}/result").status_code == 409
