"""Free web access for the agent: search openly licensed images and download assets.

The sandbox that renders videos stays offline. Downloads happen here, on the API host,
into the project's typed asset folders, so the rendered page only ever uses local files.
No API keys or paid services: image search uses Openverse (https://api.openverse.org), which
indexes openly licensed media.
"""
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .config import ALLOW_WEB_ASSETS, MAX_FILE_BYTES
from .project import require_project_dir, load_project, save_project
from .tools import CATEGORY_DIRS, CATEGORY_EXTENSIONS, _safe_name

USER_AGENT = "AI-Video-Compiler/1.0 (+https://github.com/RohitThanvi/Video-Generation-)"
MAX_REDIRECTS = 4
TIMEOUT = 20

CONTENT_TYPE_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif",
    "image/svg+xml": ".svg", "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/ogg": ".ogg", "video/mp4": ".mp4", "video/webm": ".webm",
    "font/ttf": ".ttf", "font/otf": ".otf", "font/woff": ".woff", "font/woff2": ".woff2",
    "model/gltf-binary": ".glb", "model/gltf+json": ".gltf",
    "application/json": ".json", "text/csv": ".csv",
}


def _require_enabled():
    if not ALLOW_WEB_ASSETS:
        raise PermissionError("Web asset access is disabled (set ALLOW_WEB_ASSETS=true to enable).")


def _check_public_url(url: str):
    """Only http(s) URLs whose host resolves exclusively to public addresses (anti-SSRF)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only http(s) URLs are allowed.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve {parsed.hostname}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise PermissionError(f"{parsed.hostname} resolves to a non-public address; refusing.")


def search_free_images(query: str, limit: int = 6):
    """Search Openverse for openly licensed images. Returns urls plus attribution info."""
    _require_enabled()
    if not query or not query.strip():
        raise ValueError("query is empty.")
    limit = max(1, min(int(limit), 12))
    resp = requests.get(
        "https://api.openverse.org/v1/images/",
        params={"q": query.strip(), "page_size": limit, "mature": "false"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "license": f"{r.get('license')} {r.get('license_version') or ''}".strip(),
            "creator": r.get("creator"),
            "source_page": r.get("foreign_landing_url"),
        }
        for r in resp.json().get("results", [])
        if r.get("url")
    ]


def download_asset(project_id: str, asset_type: str, url: str, filename: str | None = None):
    """Download a public URL into the project's asset folder for `asset_type`."""
    _require_enabled()
    if asset_type not in CATEGORY_EXTENSIONS:
        raise ValueError(f"Unsupported asset type: {asset_type}")
    root = require_project_dir(project_id)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _check_public_url(current)
        resp = session.get(current, stream=True, timeout=TIMEOUT, allow_redirects=False)
        if resp.is_redirect or resp.status_code in {301, 302, 303, 307, 308}:
            current = urljoin(current, resp.headers.get("Location", ""))
            resp.close()
            continue
        break
    else:
        raise ValueError("Too many redirects.")
    with resp:
        resp.raise_for_status()
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > MAX_FILE_BYTES:
            raise ValueError(f"File is larger than MAX_FILE_BYTES ({MAX_FILE_BYTES}).")
        allowed = CATEGORY_EXTENSIONS[asset_type]
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        name = _safe_name(filename or Path(urlparse(current).path).name or "download")
        suffix = Path(name).suffix.lower()
        if suffix not in allowed:
            guessed = CONTENT_TYPE_EXT.get(ctype)
            if guessed not in allowed:
                raise ValueError(f"{ctype or 'unknown type'} is not an allowed {asset_type} file.")
            name = Path(name).stem + guessed
        target = root / CATEGORY_DIRS[asset_type] / name
        target.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with open(target, "wb") as fh:
                for chunk in resp.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError(f"File is larger than MAX_FILE_BYTES ({MAX_FILE_BYTES}).")
                    fh.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    if size == 0:
        target.unlink(missing_ok=True)
        raise ValueError("Downloaded file is empty.")
    return {"path": target.relative_to(root).as_posix(), "bytes": size, "source_url": current}
