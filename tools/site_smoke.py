#!/usr/bin/env python3
"""Small safe-link smoke bot for the BlackKnightController web UI.

The bot performs GET-only crawling after optional login. It is intended for
quick "did we break the shell?" checks, not deep browser automation.
"""

from __future__ import annotations

import argparse
import html.parser
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse

import requests


BAD_TEXT_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"traceback \(most recent call last\)",
        r"internal server error",
        r"bad gateway",
        r"jinja2\.exceptions",
        r"werkzeug\.exceptions",
        r"sqlalchemy\.",
        r"undefinederror",
    )
]

DEFAULT_SEEDS = [
    "/",
    "/resources",
    "/resource",
    "/beta",
    "/pipelines",
    "/integrations",
    "/inventory",
    "/jobs",
    "/settings",
    "/api/v1/health",
    "/ready",
]


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False
        self.forms: list[dict[str, str]] = []
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag.lower() == "link" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag.lower() == "script" and attr.get("src"):
            self.links.append(attr["src"])
        elif tag.lower() == "img" and attr.get("src"):
            self.links.append(attr["src"])
        elif tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "form":
            self.forms.append(attr)
        elif tag.lower() == "input":
            name = attr.get("name")
            if name:
                self.inputs[name] = attr.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data.strip())

    @property
    def title(self) -> str:
        return " ".join(part for part in self.title_parts if part).strip()


@dataclass
class CheckResult:
    url: str
    status: int
    ok: bool
    elapsed_ms: int
    title: str = ""
    content_type: str = ""
    issue: str = ""


def normalize_url(base_url: str, href: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    joined = urljoin(base_url, href)
    joined, _fragment = urldefrag(joined)
    parsed = urlparse(joined)
    if parsed.scheme not in {"http", "https"}:
        return None
    return joined


def same_origin(url: str, root: str) -> bool:
    parsed = urlparse(url)
    root_parsed = urlparse(root)
    return (parsed.scheme, parsed.netloc) == (root_parsed.scheme, root_parsed.netloc)


def looks_mutating(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    mutating_tokens = ("delete", "logout", "remove", "redeploy", "retry", "run=", "action=run")
    return any(token in path or token in query for token in mutating_tokens)


def result_issue(response: requests.Response, body: str, allow_auth_redirect: bool) -> str:
    if response.status_code >= 500:
        return f"server error {response.status_code}"
    if response.status_code in {401, 403} and not allow_auth_redirect:
        return f"unexpected auth status {response.status_code}"
    if response.status_code >= 400 and response.status_code not in {401, 403, 404}:
        return f"http {response.status_code}"
    for pattern in BAD_TEXT_PATTERNS:
        if pattern.search(body):
            return f"matched error text: {pattern.pattern}"
    return ""


def fetch(session: requests.Session, url: str, timeout: float, allow_auth_redirect: bool) -> tuple[CheckResult, list[str]]:
    started = time.monotonic()
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return CheckResult(url=url, status=0, ok=False, elapsed_ms=elapsed_ms, issue=str(exc)), []
    elapsed_ms = int((time.monotonic() - started) * 1000)
    content_type = response.headers.get("content-type", "")
    text = response.text if "text" in content_type or "json" in content_type or "html" in content_type else ""
    parser = LinkParser()
    links: list[str] = []
    title = ""
    if "html" in content_type:
        try:
            parser.feed(text)
            links = parser.links
            title = parser.title
        except Exception:
            title = ""
    issue = result_issue(response, text[:500_000], allow_auth_redirect)
    ok = not issue
    return (
        CheckResult(
            url=url,
            status=response.status_code,
            ok=ok,
            elapsed_ms=elapsed_ms,
            title=title,
            content_type=content_type.split(";", 1)[0],
            issue=issue,
        ),
        links,
    )


def login(session: requests.Session, base_url: str, username: str, password: str, timeout: float) -> bool:
    login_url = urljoin(base_url, "/login")
    response = session.get(login_url, timeout=timeout)
    parser = LinkParser()
    parser.feed(response.text)
    data = {"username": username, "password": password}
    csrf = parser.inputs.get("csrf_token")
    if csrf:
        data["csrf_token"] = csrf
    posted = session.post(login_url, data=data, timeout=timeout, allow_redirects=True)
    return posted.status_code < 400 and "/login" not in urlparse(posted.url).path


def crawl(
    base_url: str,
    seeds: Iterable[str],
    max_pages: int,
    timeout: float,
    username: str | None,
    password: str | None,
) -> list[CheckResult]:
    session = requests.Session()
    session.headers.update({"User-Agent": "bkc-site-smoke/1.0"})
    base_url = base_url.rstrip("/") + "/"

    if username and password:
        if not login(session, base_url, username, password, timeout):
            print("WARN login did not appear to succeed; continuing unauthenticated.", file=sys.stderr)

    queue: deque[str] = deque()
    seen: set[str] = set()
    for seed in seeds:
        url = normalize_url(base_url, seed) if not seed.startswith("http") else seed
        if url and same_origin(url, base_url):
            queue.append(url)

    results: list[CheckResult] = []
    while queue and len(results) < max_pages:
        url = queue.popleft()
        if url in seen or looks_mutating(url):
            continue
        seen.add(url)
        allow_auth_redirect = not (username and password)
        result, links = fetch(session, url, timeout, allow_auth_redirect=allow_auth_redirect)
        results.append(result)
        if not result.ok:
            continue
        for href in links:
            child = normalize_url(url, href)
            if not child or child in seen or not same_origin(child, base_url) or looks_mutating(child):
                continue
            if len(seen) + len(queue) >= max_pages * 3:
                continue
            queue.append(child)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="GET-only BKC site smoke bot")
    parser.add_argument("--base-url", default=os.environ.get("BKC_SMOKE_BASE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--username", default=os.environ.get("BKC_SMOKE_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("BKC_SMOKE_PASSWORD"))
    parser.add_argument("--max-pages", type=int, default=int(os.environ.get("BKC_SMOKE_MAX_PAGES", "80")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("BKC_SMOKE_TIMEOUT", "8")))
    parser.add_argument("--seed", action="append", default=[], help="Additional seed path or URL.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    parser.add_argument("--repeat", type=int, default=1, help="Number of smoke passes to run.")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between repeated smoke passes.")
    args = parser.parse_args()

    all_failures: list[CheckResult] = []
    reports: list[dict] = []
    repeat = max(1, args.repeat)
    for pass_index in range(repeat):
        results = crawl(
            args.base_url,
            [*DEFAULT_SEEDS, *args.seed],
            max_pages=args.max_pages,
            timeout=args.timeout,
            username=args.username,
            password=args.password,
        )
        failures = [result for result in results if not result.ok]
        all_failures.extend(failures)
        reports.append(
            {
                "pass": pass_index + 1,
                "ok": not failures,
                "checked": len(results),
                "failures": [asdict(f) for f in failures],
                "results": [asdict(r) for r in results],
            }
        )
        if not args.json:
            print(f"BKC site smoke pass={pass_index + 1}/{repeat}: checked={len(results)} failures={len(failures)}")
            for result in results:
                marker = "OK " if result.ok else "BAD"
                title = f" title={result.title!r}" if result.title else ""
                issue = f" issue={result.issue}" if result.issue else ""
                print(f"{marker} {result.status:>3} {result.elapsed_ms:>5}ms {result.url}{title}{issue}")
        if pass_index < repeat - 1:
            time.sleep(max(0.0, args.interval))

    if args.json:
        print(json.dumps({"ok": not all_failures, "passes": reports}, indent=2))
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
