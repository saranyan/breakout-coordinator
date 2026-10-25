"""Immutable snapshots; private URLs are supplied via existing document tools."""
from pathlib import Path
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .protocol import digest

MAX_DOCUMENT = 2 * 1024 * 1024


def snapshot(source, snapshot_file=None, revision=None, comments_complete=False):
    parsed = urlparse(source)
    if snapshot_file:
        raw = Path(snapshot_file).read_bytes()
        path = str(Path(snapshot_file).resolve())
    elif parsed.scheme in ("http", "https"):
        if "notion." in (parsed.hostname or ""):
            raise ValueError("Export the full Notion page and all comment threads with existing tools; use --snapshot and --comments-complete")
        if parsed.scheme != "https":
            raise ValueError("URL documents must use HTTPS")
        with urlopen(Request(source, headers={"User-Agent": "breakout/0.1"}), timeout=15) as response:
            if urlparse(response.url).scheme != "https":
                raise ValueError("Document redirected away from HTTPS")
            raw = response.read(MAX_DOCUMENT + 1)
        path = None
    elif parsed.scheme:
        raise ValueError("Unsupported document scheme")
    else:
        path = str(Path(source).expanduser().resolve())
        raw = Path(path).read_bytes()
    if len(raw) > MAX_DOCUMENT:
        raise ValueError("Document exceeds 2 MiB snapshot limit")
    text = raw.decode("utf-8")
    if "notion." in (parsed.hostname or "") and not comments_complete:
        raise ValueError("Confirm all Notion comment threads were included with --comments-complete")
    return dict(source=source, local_path=path, text=text, sha256=digest(text),
                source_revision=revision, captured=time.time(), comments_complete=comments_complete,
                freshness="local hash check" if not parsed.scheme else "external revision must be rechecked")


def freshness(document):
    if document.get('kind') == 'task':
        return 'immutable task brief; no external document'
    if not urlparse(document["source"]).scheme and document.get("local_path"):
        try:
            return "unchanged" if digest(Path(document["local_path"]).read_text()) == document["sha256"] else "stale"
        except (OSError, UnicodeError):
            return "unavailable"
    return "unverified external source; recheck before applying"
