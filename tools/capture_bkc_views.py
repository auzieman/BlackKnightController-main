#!/usr/bin/env python3
"""Capture selected BlackKnightController web views as PNG documentation artifacts.

The tool is intentionally small and manifest-driven:

    python tools/capture_bkc_views.py
    python tools/capture_bkc_views.py --base-url http://swarm1.lab.auzietek.com:5000
    python tools/capture_bkc_views.py --view bkc-beta-resources

Install the browser runtime when needed:

    python -m pip install playwright
    python -m playwright install chromium

For container use, mount the repo and run from the repository root.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urljoin


DEFAULT_MANIFEST = Path("docs/bkc-view-captures.json")


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_base_url(value: str) -> str:
    return value.rstrip("/") + "/"


def view_url(base_url: str, path: str) -> str:
    return urljoin(normalize_base_url(base_url), path.lstrip("/"))


def coerce_int(value, default: int) -> int:
    if value is None:
        return default
    return int(value)


def login_if_configured(page, base_url: str, args: argparse.Namespace) -> None:
    username = args.username or os.getenv("BKC_CAPTURE_USERNAME")
    password = args.password or os.getenv("BKC_CAPTURE_PASSWORD")
    if not username or not password:
        return
    login_path = args.login_path or "/login"
    login_url = view_url(base_url, login_path)
    print(f"login: {login_url} as {username}")
    page.goto(login_url, wait_until="networkidle")
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.locator("button[type='submit'], input[type='submit']").first.click()
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        raise SystemExit("login did not leave the login page; check BKC_CAPTURE_USERNAME/BKC_CAPTURE_PASSWORD")


def apply_view_actions(page, view: dict, timeout_ms: int, settle_ms: int) -> None:
    for action in view.get("actions", []):
        kind = action.get("kind", "click")
        selector = action.get("selector")
        if not selector:
            continue
        locator = page.locator(selector).first
        if callable(locator):
            locator = locator()
        if kind == "click":
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.click()
        elif kind == "fill":
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.fill(str(action.get("value", "")))
        elif kind == "check":
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.check()
        else:
            print(f"warning: unknown action kind ignored: {kind}")
            continue
        delay = int(action.get("settle_ms", settle_ms))
        if delay:
            page.wait_for_timeout(delay)


def capture_views(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required. Install with: python -m pip install playwright && "
            "python -m playwright install chromium"
        ) from exc

    manifest = load_manifest(args.manifest)
    base_url = args.base_url or manifest.get("base_url")
    if not base_url:
        raise SystemExit("base_url is required in the manifest or via --base-url")

    output_dir = Path(args.output_dir or manifest.get("output_dir", "docs/images/generated"))
    output_dir.mkdir(parents=True, exist_ok=True)

    viewport = manifest.get("viewport", {})
    default_width = int(args.width or viewport.get("width", 1440))
    default_height = int(args.height or viewport.get("height", 1000))

    selected_names = set(args.view or [])
    views = [
        view
        for view in manifest.get("views", [])
        if not selected_names or view.get("name") in selected_names
    ]
    if selected_names and len(views) != len(selected_names):
        found = {view.get("name") for view in views}
        missing = ", ".join(sorted(selected_names - found))
        raise SystemExit(f"unknown view(s): {missing}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": default_width, "height": default_height}, device_scale_factor=args.scale)
        page.set_default_timeout(args.timeout_ms)
        login_if_configured(page, base_url, args)

        for view in views:
            name = view["name"]
            url = view_url(base_url, view.get("path", "/"))
            output_path = output_dir / f"{name}.png"
            print(f"capture {name}: {url} -> {output_path}")
            width = coerce_int(args.width or view.get("width"), default_width)
            height = coerce_int(args.height or view.get("height"), default_height)
            page.set_viewport_size({"width": width, "height": height})
            page.goto(url, wait_until="networkidle")
            wait_for = view.get("wait_for")
            if wait_for:
                try:
                    page.locator(wait_for).first.wait_for(state="visible", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print(f"warning: wait selector did not become visible for {name}: {wait_for}")
            apply_view_actions(page, view, args.timeout_ms, args.settle_ms)
            if args.settle_ms:
                page.wait_for_timeout(args.settle_ms)
            capture_selector = args.selector or view.get("capture_selector")
            if capture_selector:
                page.locator(capture_selector).first.screenshot(path=str(output_path))
            else:
                page.screenshot(path=str(output_path), full_page=args.full_page or bool(view.get("full_page")))

        browser.close()

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base-url", help="Override BKC base URL")
    parser.add_argument("--output-dir", help="Override output directory")
    parser.add_argument("--view", action="append", help="Capture one named view; repeatable")
    parser.add_argument("--width", type=int, help="Viewport width")
    parser.add_argument("--height", type=int, help="Viewport height")
    parser.add_argument("--scale", type=float, default=1.0, help="Device scale factor")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--settle-ms", type=int, default=1200, help="Extra delay after selectors become visible")
    parser.add_argument("--full-page", action="store_true", help="Capture the full scrollable page")
    parser.add_argument("--selector", help="Capture one selector instead of the viewport/full page")
    parser.add_argument("--login-path", default="/login")
    parser.add_argument("--username", help="BKC login username; prefer BKC_CAPTURE_USERNAME")
    parser.add_argument("--password", help="BKC login password; prefer BKC_CAPTURE_PASSWORD")
    return parser.parse_args()


def main() -> int:
    return capture_views(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
