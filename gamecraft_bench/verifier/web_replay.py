"""Replay a demo trace in Chromium and record its X11 display."""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from .. import config as cfg
from .replay import _start_xvfb, _stop, _wait_for_ffmpeg_capture
from .replay_common import ReplayError, ReplayResult

_KEYCODES: dict[str, str] = {
    "ESCAPE": "Escape",
    "ENTER": "Enter",
    "SPACE": "Space",
    "TAB": "Tab",
    "BACKSPACE": "Backspace",
    "DELETE": "Delete",
    "UP": "ArrowUp",
    "DOWN": "ArrowDown",
    "LEFT": "ArrowLeft",
    "RIGHT": "ArrowRight",
    "SHIFT": "Shift",
    "CTRL": "Control",
    "ALT": "Alt",
    **{c: c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{str(d): str(d) for d in range(10)},
}
_MOUSE_EVENTS = {"mouse_move", "mouse_click", "mouse_down", "mouse_up"}
_KEY_EVENTS = {"key_press", "key_down", "key_up"}
_EVENT_TYPES = _MOUSE_EVENTS | _KEY_EVENTS | {"wait"}


def replay_web_trace(
    *,
    project_dir: Path,
    trace_path: Path,
    output_mp4: Path,
    runtime: dict,
    viewport: tuple[int, int] = (1280, 720),
    record_size: tuple[int, int] | None = (854, 480),
    fps: int = 30,
    log_dir: Path | None = None,
    max_replay_seconds: float = 90.0,
    max_total_seconds: float | None = None,
) -> ReplayResult:
    project_dir = Path(project_dir).resolve()
    trace_path = Path(trace_path).resolve()
    output_mp4 = Path(output_mp4).resolve()
    log_dir = Path(log_dir).resolve() if log_dir is not None else None
    log_hint = f"; see logs in {log_dir}" if log_dir is not None else "; pass log_dir to retain replay logs"

    xvfb: subprocess.Popen | None = None
    server: subprocess.Popen | None = None
    ffmpeg: subprocess.Popen | None = None
    playwright: Any = None
    context: Any = None
    page: Any = None
    profile_dir: str | None = None
    log_handles: list[BinaryIO | TextIO] = []
    browser_log: TextIO | None = None
    page_errors: list[str] = []
    page_crashes: list[str] = []
    timing_events: list[dict[str, Any]] = []
    trace_seconds = 0.0
    ffmpeg_stopped = False

    try:
        _validate_replay_options(
            viewport, record_size, fps, max_replay_seconds, max_total_seconds
        )
        total_deadline = (
            time.monotonic() + max_total_seconds
            if max_total_seconds is not None else None
        )
        if not project_dir.is_dir():
            raise ReplayError(f"web project directory does not exist: {project_dir}")
        for tool in ("Xvfb", "ffmpeg"):
            if shutil.which(tool) is None:
                raise ReplayError(f"required tool not on PATH: {tool}")

        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)

        runtime_config = _validate_runtime(runtime, project_dir)
        trace = _load_trace(trace_path, viewport=viewport, fps=fps)
        scenario = trace["scenario"]
        events = trace["events"]
        replay_frames = max(
            [trace["duration_frames"], *(event["frame"] for event in events)]
        )
        trace_seconds = replay_frames / fps
        if trace_seconds > max_replay_seconds:
            raise ReplayError(
                f"trace lasts {trace_seconds:.2f}s, exceeding "
                f"max_replay_seconds={max_replay_seconds}s"
            )

        port = _allocate_loopback_port()
        start_cmd = runtime_config["start_cmd"].replace("{port}", str(port))
        base_url = runtime_config["base_url"].replace("{port}", str(port))
        parsed_base = urllib.parse.urlsplit(base_url)
        if parsed_base.scheme not in ("http", "https") or not parsed_base.netloc:
            raise ReplayError(f"runtime base_url must be an absolute HTTP(S) URL: {base_url!r}")
        if parsed_base.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ReplayError(f"runtime base_url must use a loopback host: {base_url!r}")
        base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        target_url = base_origin + "/"
        if scenario is not None:
            target_url += "?" + urllib.parse.urlencode({"scenario": scenario})

        server_log = _open_binary_log(log_dir, "server.log", log_handles)
        env = dict(os.environ)
        server = subprocess.Popen(
            start_cmd,
            shell=True,
            cwd=runtime_config["cwd"],
            env=env,
            stdout=server_log,
            stderr=server_log,
            start_new_session=True,
        )
        startup_deadline = time.monotonic() + runtime_config["startup_timeout_seconds"]
        if total_deadline is not None:
            startup_deadline = min(startup_deadline, total_deadline)
        _wait_for_server(server, base_url, startup_deadline, log_hint)

        xvfb_log = _open_binary_log(log_dir, "xvfb.log", log_handles)
        screen_size = (viewport[0] + 64, viewport[1] + 256)
        xvfb, display_n = _start_xvfb(xvfb_log, viewport=screen_size)
        display = f":{display_n}"
        env.update({"DISPLAY": display, "LP_NUM_THREADS": "1"})

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ReplayError(
                "Playwright is required for web replay; install its Python package "
                f"and Chromium browser{log_hint}"
            ) from exc

        browser_log = _open_text_log(log_dir, "browser.log", log_handles)
        _browser_log(browser_log, f"launch target={target_url} display={display}")
        profile_dir = tempfile.mkdtemp(prefix="gamecraft-web-replay-")
        default_profile = Path(profile_dir) / "Default"
        default_profile.mkdir()
        (default_profile / "Preferences").write_text(
            json.dumps({"translate": {"enabled": False}}), encoding="utf-8"
        )
        playwright = sync_playwright().start()
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            service_workers="block",
            viewport={"width": viewport[0], "height": viewport[1]},
            device_scale_factor=1,
            env=env,
            executable_path=cfg.CHROMIUM_BIN,
            timeout=_remaining_ms(startup_deadline, "browser launch"),
            args=[
                "--window-position=0,0",
                f"--window-size={viewport[0]},{viewport[1]}",
                "--force-device-scale-factor=1",
                "--disable-translate",
                "--disable-features=Translate,TranslateUI",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(max(1.0, _remaining_ms(startup_deadline, "page setup")))

        def on_console(message: Any) -> None:
            with contextlib.suppress(Exception):
                _browser_log(browser_log, f"console.{message.type}: {message.text}")

        def on_page_error(error: Any) -> None:
            message = str(error)
            page_errors.append(message)
            _browser_log(browser_log, f"pageerror: {message}")

        def on_request_failed(request: Any) -> None:
            with contextlib.suppress(Exception):
                _browser_log(
                    browser_log,
                    f"requestfailed: {request.method} {request.url}: {request.failure}",
                )

        def on_crash() -> None:
            page_crashes.append("page crashed")
            _browser_log(browser_log, "crash: page crashed")

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("requestfailed", on_request_failed)
        page.on("crash", on_crash)

        def route_request(route: Any, request: Any) -> None:
            parsed = urllib.parse.urlsplit(request.url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme in ("http", "https") and origin != base_origin:
                _browser_log(browser_log, f"blocked: {request.method} {request.url}")
                route.abort("blockedbyclient")
            else:
                route.continue_()

        context.route("**/*", route_request)
        capture_origin = _page_content_origin(page, viewport, screen_size)
        _browser_log(browser_log, f"capture origin={capture_origin} viewport={viewport}")
        try:
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=_remaining_ms(startup_deadline, "page navigation"),
            )
            page.wait_for_function(
                runtime_config["ready_expression"],
                timeout=_remaining_ms(startup_deadline, "page readiness"),
            )
        except ReplayError:
            raise
        except Exception as exc:
            _ensure_healthy(page, server, None, page_errors, page_crashes)
            raise ReplayError(
                f"web page did not become ready within "
                f"{runtime_config['startup_timeout_seconds']}s: {exc}{log_hint}"
            ) from exc
        _ensure_healthy(page, server, None, page_errors, page_crashes)

        dimensions = page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight, "
            "dpr: window.devicePixelRatio})"
        )
        expected = {"width": viewport[0], "height": viewport[1], "dpr": 1}
        if dimensions != expected:
            raise ReplayError(
                f"Chromium viewport mismatch: expected {expected}, got {dimensions}{log_hint}"
            )

        ffmpeg_log_path = log_dir / "ffmpeg.log" if log_dir is not None else None
        ffmpeg_log = _open_binary_log(log_dir, "ffmpeg.log", log_handles)
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-draw_mouse", "0",
            "-framerate", str(fps),
            "-video_size", f"{viewport[0]}x{viewport[1]}",
            "-i", f"{display}+{capture_origin[0]},{capture_origin[1]}",
        ]
        if record_size is not None and record_size != viewport:
            ffmpeg_cmd += [
                "-vf", f"scale={record_size[0]}:{record_size[1]}:flags=lanczos"
            ]
        ffmpeg_cmd += [
            "-t", f"{trace_seconds + 2.0:.3f}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(output_mp4),
        ]
        ffmpeg = subprocess.Popen(
            ffmpeg_cmd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=ffmpeg_log,
            stderr=ffmpeg_log,
        )
        ffmpeg_timeout = 15.0
        if total_deadline is not None:
            ffmpeg_timeout = min(ffmpeg_timeout, total_deadline - time.monotonic())
        if ffmpeg_timeout <= 0:
            raise ReplayError("web total replay budget exceeded before ffmpeg capture")
        _wait_for_ffmpeg_capture(ffmpeg, ffmpeg_log_path, timeout=ffmpeg_timeout)
        _ensure_healthy(page, server, ffmpeg, page_errors, page_crashes)

        t0 = time.monotonic()
        deadline = t0 + max_replay_seconds
        if total_deadline is not None:
            deadline = min(deadline, total_deadline)
        for index, event in enumerate(events):
            target = t0 + event["frame"] / fps
            _wait_until_healthy(
                target,
                deadline=deadline,
                page=page,
                server=server,
                ffmpeg=ffmpeg,
                page_errors=page_errors,
                page_crashes=page_crashes,
            )
            _post_event(page, event)
            timing_events.append({
                "index": index,
                "frame": event["frame"],
                "type": event["type"],
                "scheduled_seconds": event["frame"] / fps,
                "actual_seconds": time.monotonic() - t0,
            })
            _write_timing(log_dir, fps, trace_seconds, timing_events)

        _wait_until_healthy(
            t0 + replay_frames / fps,
            deadline=deadline,
            page=page,
            server=server,
            ffmpeg=ffmpeg,
            page_errors=page_errors,
            page_crashes=page_crashes,
        )
        _stop_ffmpeg(ffmpeg)
        ffmpeg_stopped = True
        _ensure_healthy(page, server, None, page_errors, page_crashes)

        if not output_mp4.exists() or output_mp4.stat().st_size == 0:
            raise ReplayError(f"recording is empty: {output_mp4}{log_hint}")

        context.close()
        context = None
        playwright.stop()
        playwright = None
        runtime_returncode = _stop_server(server)
        server = None
        return ReplayResult(
            output_mp4=output_mp4,
            duration_seconds=trace_seconds,
            runtime_returncode=runtime_returncode,
        )
    except ReplayError:
        raise
    except Exception as exc:
        raise ReplayError(
            f"web replay failed: {type(exc).__name__}: {exc}{log_hint}"
        ) from exc
    finally:
        if ffmpeg is not None and not ffmpeg_stopped:
            with contextlib.suppress(Exception):
                _stop_ffmpeg(ffmpeg)
        if context is not None:
            with contextlib.suppress(Exception):
                context.close()
        if playwright is not None:
            with contextlib.suppress(Exception):
                playwright.stop()
        if server is not None:
            with contextlib.suppress(Exception):
                _stop_server(server)
        if xvfb is not None:
            with contextlib.suppress(Exception):
                _stop(xvfb)
        if profile_dir is not None:
            with contextlib.suppress(OSError):
                shutil.rmtree(profile_dir)
        with contextlib.suppress(Exception):
            _write_timing(log_dir, fps, trace_seconds, timing_events)
        for handle in log_handles:
            with contextlib.suppress(Exception):
                handle.close()


def _validate_replay_options(
    viewport: tuple[int, int],
    record_size: tuple[int, int] | None,
    fps: int,
    max_replay_seconds: float,
    max_total_seconds: float | None,
) -> None:
    if (
        len(viewport) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in viewport)
    ):
        raise ReplayError(f"viewport must contain two positive integers: {viewport!r}")
    if record_size is not None and (
        len(record_size) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in record_size)
    ):
        raise ReplayError(f"record_size must contain two positive integers: {record_size!r}")
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ReplayError(f"fps must be a positive integer: {fps!r}")
    for name, value in (
        ("max_replay_seconds", max_replay_seconds),
        ("max_total_seconds", max_total_seconds),
    ):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ReplayError(f"{name} must be finite and positive: {value!r}")


def _validate_runtime(runtime: dict, project_dir: Path) -> dict[str, Any]:
    if not isinstance(runtime, dict):
        raise ReplayError("web runtime must be a JSON object")
    required = ("type", "cwd", "start_cmd", "base_url", "ready_expression")
    missing = [key for key in required if key not in runtime]
    if missing:
        raise ReplayError(f"web runtime is missing required fields: {', '.join(missing)}")
    if runtime["type"] != "web":
        raise ReplayError(f"web runtime type must be 'web', got {runtime['type']!r}")
    for key in ("start_cmd", "base_url", "ready_expression"):
        if not isinstance(runtime[key], str) or not runtime[key].strip():
            raise ReplayError(f"web runtime {key} must be a non-empty string")
    if "{port}" not in runtime["start_cmd"]:
        raise ReplayError("web runtime start_cmd must contain {port}")
    if "{port}" not in runtime["base_url"]:
        raise ReplayError("web runtime base_url must contain {port}")
    if not isinstance(runtime["cwd"], (str, os.PathLike)):
        raise ReplayError("web runtime cwd must be a path string")
    cwd = Path(runtime["cwd"]).expanduser()
    if not cwd.is_absolute():
        cwd = project_dir / cwd
    cwd = cwd.resolve()
    if not cwd.is_dir():
        raise ReplayError(f"web runtime cwd does not exist: {cwd}")
    startup_timeout = runtime.get("startup_timeout_seconds", 30.0)
    if (
        isinstance(startup_timeout, bool)
        or not isinstance(startup_timeout, (int, float))
        or not math.isfinite(startup_timeout)
        or startup_timeout <= 0
    ):
        raise ReplayError("runtime startup_timeout_seconds must be finite and positive")
    return {
        "cwd": str(cwd),
        "start_cmd": runtime["start_cmd"],
        "base_url": runtime["base_url"],
        "ready_expression": runtime["ready_expression"],
        "startup_timeout_seconds": float(startup_timeout),
    }


def _load_trace(trace_path: Path, *, viewport: tuple[int, int], fps: int) -> dict[str, Any]:
    try:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError(f"could not read web replay trace {trace_path}: {exc}") from exc
    if not isinstance(trace, dict):
        raise ReplayError(f"web replay trace must be a JSON object: {trace_path}")
    duration_frames = trace.get("duration_frames", 0)
    if isinstance(duration_frames, bool) or not isinstance(duration_frames, int) or duration_frames < 0:
        raise ReplayError(f"duration_frames must be a non-negative integer: {duration_frames!r}")
    scenario = trace.get("scenario")
    if scenario is not None and (
        isinstance(scenario, bool) or not isinstance(scenario, (str, int, float))
    ):
        raise ReplayError(f"scenario must be a string, number, or null: {scenario!r}")
    raw_events = trace.get("events", [])
    if not isinstance(raw_events, list):
        raise ReplayError("trace events must be a list")

    events: list[dict[str, Any]] = []
    previous_frame = -1
    for index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            raise ReplayError(f"trace event {index} must be an object")
        event = dict(raw_event)
        frame = event.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise ReplayError(f"trace event {index} has invalid frame: {frame!r}")
        if frame < previous_frame:
            raise ReplayError(
                f"trace events are not sorted by frame at event {index}: "
                f"{frame} < {previous_frame}"
            )
        previous_frame = frame
        event_type = event.get("type")
        if event_type not in _EVENT_TYPES:
            raise ReplayError(f"trace event {index} has unknown type: {event_type!r}")
        if event_type in _MOUSE_EVENTS:
            for coordinate, limit in (("x", viewport[0]), ("y", viewport[1])):
                value = event.get(coordinate)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0 <= value < limit
                ):
                    raise ReplayError(
                        f"trace event {index} has invalid {coordinate} coordinate "
                        f"for viewport {viewport}: {value!r}"
                    )
            if event_type != "mouse_move":
                button = str(event.get("button", "left")).strip().lower()
                if button not in ("left", "right"):
                    raise ReplayError(f"trace event {index} has unknown mouse button: {button!r}")
                event["button"] = button
        elif event_type in _KEY_EVENTS:
            keycode = event.get("keycode")
            if not isinstance(keycode, str):
                raise ReplayError(f"trace event {index} has non-string keycode: {keycode!r}")
            normalized = _KEYCODES.get(keycode.strip().upper())
            if normalized is None:
                raise ReplayError(f"trace event {index} has unknown keycode: {keycode!r}")
            event["keycode"] = normalized
        events.append(event)
    return {
        "scenario": scenario,
        "duration_frames": duration_frames,
        "events": events,
        "fps": fps,
    }


def _allocate_loopback_port() -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    except OSError as exc:
        raise ReplayError(f"could not allocate a loopback port: {exc}") from exc


def _wait_for_server(
    proc: subprocess.Popen,
    base_url: str,
    deadline: float,
    log_hint: str,
) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = ""
    while True:
        if proc.poll() is not None:
            raise ReplayError(
                f"web server exited early with code {proc.returncode}{log_hint}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f": {last_error}" if last_error else ""
            raise ReplayError(f"web server did not become ready at {base_url}{detail}{log_hint}")
        try:
            with opener.open(base_url, timeout=min(1.0, remaining)) as response:
                if 200 <= response.status < 400:
                    return
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _remaining_ms(deadline: float, stage: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReplayError(f"web startup timed out during {stage}")
    return remaining * 1000.0


def _page_content_origin(
    page: Any,
    viewport: tuple[int, int],
    screen_size: tuple[int, int],
) -> tuple[int, int]:
    page.evaluate(
        """() => {
            window.__gamecraftCaptureOrigin = null;
            document.addEventListener('mousemove', event => {
                window.__gamecraftCaptureOrigin = {
                    x: event.screenX - event.clientX,
                    y: event.screenY - event.clientY,
                };
            }, {once: true, capture: true});
        }"""
    )
    page.mouse.move(0, 0)
    page.wait_for_function(
        "window.__gamecraftCaptureOrigin !== null", timeout=5000
    )
    origin = page.evaluate("window.__gamecraftCaptureOrigin")
    x, y = origin.get("x"), origin.get("y")
    if (
        isinstance(x, bool) or not isinstance(x, (int, float))
        or isinstance(y, bool) or not isinstance(y, (int, float))
        or not math.isfinite(x) or not math.isfinite(y)
        or x < 0 or y < 0
        or x + viewport[0] > screen_size[0]
        or y + viewport[1] > screen_size[1]
    ):
        raise ReplayError(
            f"invalid Chromium content origin {origin!r} for viewport {viewport} "
            f"on X11 screen {screen_size}"
        )
    return int(x), int(y)


def _ensure_healthy(
    page: Any,
    server: subprocess.Popen,
    ffmpeg: subprocess.Popen | None,
    page_errors: list[str],
    page_crashes: list[str],
) -> None:
    if server.poll() is not None:
        raise ReplayError(f"web server exited early with code {server.returncode}; see server.log")
    if ffmpeg is not None and ffmpeg.poll() is not None:
        raise ReplayError(f"ffmpeg exited early with code {ffmpeg.returncode}; see ffmpeg.log")
    if page_crashes:
        raise ReplayError(f"Chromium page crashed; see browser.log: {page_crashes[-1]}")
    if page_errors:
        raise ReplayError(f"Chromium page error; see browser.log: {page_errors[-1]}")
    page.evaluate("() => 1")
    if page_crashes:
        raise ReplayError(f"Chromium page crashed; see browser.log: {page_crashes[-1]}")
    if page_errors:
        raise ReplayError(f"Chromium page error; see browser.log: {page_errors[-1]}")


def _wait_until_healthy(
    target: float,
    *,
    deadline: float,
    page: Any,
    server: subprocess.Popen,
    ffmpeg: subprocess.Popen,
    page_errors: list[str],
    page_crashes: list[str],
) -> None:
    while True:
        _ensure_healthy(page, server, ffmpeg, page_errors, page_crashes)
        now = time.monotonic()
        if now >= deadline and target < deadline:
            raise ReplayError("web replay exceeded its input timeline deadline")
        if now >= target:
            return
        time.sleep(min(0.05, max(0.0, target - now)))


def _post_event(page: Any, event: dict[str, Any]) -> None:
    event_type = event["type"]
    if event_type == "wait":
        return
    if event_type == "mouse_move":
        page.mouse.move(event["x"], event["y"])
        return
    if event_type == "mouse_click":
        page.mouse.click(event["x"], event["y"], button=event["button"])
        return
    if event_type in ("mouse_down", "mouse_up"):
        page.mouse.move(event["x"], event["y"])
        if event_type == "mouse_down":
            page.mouse.down(button=event["button"])
        else:
            page.mouse.up(button=event["button"])
        return
    if event_type == "key_press":
        page.keyboard.press(event["keycode"])
    elif event_type == "key_down":
        page.keyboard.down(event["keycode"])
    else:
        page.keyboard.up(event["keycode"])


def _stop_ffmpeg(proc: subprocess.Popen) -> int:
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
    if proc.stdin is not None:
        with contextlib.suppress(Exception):
            proc.stdin.close()
    return int(proc.returncode)


def _stop_server(proc: subprocess.Popen) -> int:
    returncode = proc.poll()
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    if returncode is not None:
        return int(returncode)
    try:
        return int(proc.wait(timeout=5))
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        return int(proc.wait(timeout=2))


def _open_binary_log(
    log_dir: Path | None,
    name: str,
    handles: list[BinaryIO | TextIO],
) -> BinaryIO | int:
    if log_dir is None:
        return subprocess.DEVNULL
    handle = (log_dir / name).open("wb")
    handles.append(handle)
    return handle


def _open_text_log(
    log_dir: Path | None,
    name: str,
    handles: list[BinaryIO | TextIO],
) -> TextIO | None:
    if log_dir is None:
        return None
    handle = (log_dir / name).open("w", encoding="utf-8", buffering=1)
    handles.append(handle)
    return handle


def _browser_log(handle: TextIO | None, message: str) -> None:
    if handle is not None:
        with contextlib.suppress(Exception):
            handle.write(f"{time.monotonic():.6f} {message}\n")


def _write_timing(
    log_dir: Path | None,
    fps: int,
    trace_seconds: float,
    events: list[dict[str, Any]],
) -> None:
    if log_dir is None:
        return
    (log_dir / "timing.json").write_text(
        json.dumps({"fps": fps, "trace_seconds": trace_seconds, "events": events}, indent=2)
        + "\n",
        encoding="utf-8",
    )


__all__ = ["replay_web_trace"]
