#!/usr/bin/env python3
"""Upload a blog post to vidyut.net (WordPress REST API).

Reads credentials from the JSON reference file (default:
~/.config/opencode/vidyut.net.json). Posts are created as drafts unless
--publish is given.

Examples:
  python scripts/upload_blog.py --title "Lekhak update" --file post.html
  python scripts/upload_blog.py --title "Lekhak update" --file post.md --format markdown --publish
  cat post.html | python scripts/upload_blog.py --title "Lekhak update"
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CREDS = Path.home() / ".config/opencode/vidyut.net.json"
DEFAULT_SITE = "https://vidyut.net"


def load_creds(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def auth_header(creds: dict) -> str:
    username = creds.get("username", "")
    password = creds.get("applicationPassword") or creds.get("application_password") or ""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def api_request(site: str, endpoint: str, creds: dict, payload: dict | None = None,
                method: str = "POST") -> dict:
    url = site.rstrip("/") + endpoint
    data = None
    headers = {
        "Authorization": auth_header(creds),
        "Accept": "application/json",
        "User-Agent": "Lekhak/1.0 (+https://vidyut.info/downloads/lekhak/) WordPress-REST",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} {exc.reason}: {detail}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload a blog post to vidyut.net")
    parser.add_argument("--creds", default=str(DEFAULT_CREDS),
                        help="Path to WordPress credentials JSON")
    parser.add_argument("--site", default=None,
                        help="Site base URL (default: from creds or vidyut.net)")
    parser.add_argument("--title", required=True, help="Post title")
    parser.add_argument("--file", help="Content file (HTML/Markdown/text). Reads stdin if omitted.")
    parser.add_argument("--format", choices=["html", "markdown", "text"], default="html",
                        help="Content format hint (content is sent as-is; markdown/text wrapped in <pre> unless html)")
    parser.add_argument("--status", choices=["draft", "publish", "pending", "private"], default="draft",
                        help="Post status (default: draft)")
    parser.add_argument("--publish", action="store_true", help="Shortcut for --status publish")
    parser.add_argument("--excerpt", default="", help="Optional excerpt")
    parser.add_argument("--categories", default="", help="Comma-separated category IDs")
    parser.add_argument("--tags", default="", help="Comma-separated tag IDs")
    args = parser.parse_args(argv)

    creds_path = Path(args.creds).expanduser()
    if not creds_path.is_file():
        raise SystemExit(f"Credentials file not found: {creds_path}")
    creds = load_creds(creds_path)
    site = args.site or creds.get("site") or (creds.get("sites") or [DEFAULT_SITE])[0] or DEFAULT_SITE

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()

    if args.format == "markdown":
        # Send markdown as content; WordPress may store as-is depending on setup.
        # Wrap in a pre block for safety if needed, but prefer raw.
        body = content
    elif args.format == "text":
        import html
        body = "<pre>" + html.escape(content) + "</pre>"
    else:
        body = content

    status = "publish" if args.publish else args.status
    payload = {
        "title": args.title,
        "content": body,
        "status": status,
    }
    if args.excerpt:
        payload["excerpt"] = args.excerpt
    if args.categories:
        payload["categories"] = [int(x.strip()) for x in args.categories.split(",") if x.strip()]
    if args.tags:
        payload["tags"] = [int(x.strip()) for x in args.tags.split(",") if x.strip()]

    result = api_request(site, "/wp-json/wp/v2/posts", creds, payload=payload)
    link = result.get("link") or result.get("guid", {}).get("rendered") or ""
    post_id = result.get("id")
    print(f"Created post {post_id} ({result.get('status', status)})")
    if link:
        print(link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
