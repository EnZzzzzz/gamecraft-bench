#!/usr/bin/env python3
"""Capture a ready Web game in headless Chromium using Playwright."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def target_url(url: str, scenario: str | None) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("--url must be an absolute http:// or https:// URL")
    if scenario is None:
        return url

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "scenario"
    ]
    query.append(("scenario", scenario))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Screenshot a Web game after its ready flag and a 30fps frame delay."
    )
    parser.add_argument("--url", required=True, help="Absolute URL of the running game")
    parser.add_argument("--out", required=True, help="Output image path")
    parser.add_argument("--scenario", help="Set the scenario query parameter")
    parser.add_argument(
        "--frames",
        type=nonnegative_int,
        default=60,
        help="Post-ready delay in 30fps frames (default: 60)",
    )
    parser.add_argument("--width", type=positive_int, default=1280)
    parser.add_argument("--height", type=positive_int, default=720)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        url = target_url(args.url, args.scenario)
        output = Path(args.out).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": args.width, "height": args.height},
                    device_scale_factor=1,
                    service_workers="block",
                )
                page = context.new_page()
                page.goto(url, wait_until="load", timeout=30_000)
                page.wait_for_function(
                    "window.__GAME_READY__ === true", timeout=30_000
                )
                page.wait_for_timeout(args.frames * 1000 / 30)
                page.screenshot(path=str(output), full_page=False)
            finally:
                browser.close()
    except Exception as exc:
        print(f"screenshot_web: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
