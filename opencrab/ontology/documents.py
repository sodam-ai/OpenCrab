"""Read source documents to text, routing office and Korean formats through Kordoc 4.x.

Kordoc (``npm install --global kordoc@latest``) parses HWP/HWPX, PDF, DOCX, XLSX,
PPTX and HWPML into Markdown. 4.x is required for HWPX, DOCX and XLSX; older
releases are skipped for those formats. Plain text formats are read directly.
Every read returns parser metadata so a failed parse is recorded, never hidden.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

KORDOC_RECOMMENDED_MAJOR = 4
KORDOC_EXTENSIONS = frozenset({".pdf", ".hwp", ".hwp3", ".hwpml", ".xls", ".pptx"})
# Kordoc 4.x parses these natively; earlier releases are not used for them.
KORDOC_V4_EXTENSIONS = frozenset({".hwpx", ".docx", ".xlsx"})
DOCUMENT_EXTENSIONS = KORDOC_EXTENSIONS | KORDOC_V4_EXTENSIONS
TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".py", ".json", ".jsonl", ".csv", ".tsv",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".toml", ".ts", ".js",
})
DEFAULT_TIMEOUT = int(os.getenv("KORDOC_TIMEOUT", "180"))


@dataclass
class ReadResult:
    text: str
    parser: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def resolve_kordoc_command(kordoc_bin: str = "", which: Callable[[str], str | None] = shutil.which) -> list[str]:
    configured = kordoc_bin.strip() or os.getenv("KORDOC_BIN", "").strip()
    if configured:
        return shlex.split(configured)
    found = which("kordoc")
    return [found] if found else []


_MAJOR_CACHE: dict[tuple[str, ...], int | None] = {}


def kordoc_major_version(command: list[str], run: Callable[..., Any] = subprocess.run) -> int | None:
    if not command:
        return None
    key = tuple(command)
    if key not in _MAJOR_CACHE:
        major: int | None = None
        try:
            completed = run([*command, "--version"], capture_output=True, text=True, timeout=10, check=False)
            match = re.search(r"(\d+)\.\d+", completed.stdout or completed.stderr or "")
            major = int(match.group(1)) if match else None
        except Exception:
            major = None
        _MAJOR_CACHE[key] = major
    return _MAJOR_CACHE[key]


def read_with_kordoc(
    path: Path,
    *,
    kordoc_bin: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> ReadResult | None:
    """Parse one document with Kordoc; None when Kordoc is missing or too old for the format."""
    command = resolve_kordoc_command(kordoc_bin, which)
    if not command:
        return None
    major = kordoc_major_version(command, run)
    suffix = path.suffix.lower()
    if suffix in KORDOC_V4_EXTENSIONS and (major is None or major < KORDOC_RECOMMENDED_MAJOR):
        return None
    # 4.x: skip image bytes so large scanned documents do not bloat the JSON on stdout.
    extra = ["--no-images"] if major is not None and major >= KORDOC_RECOMMENDED_MAJOR else []
    try:
        completed = run(
            [*command, str(path), "--format", "json", "--silent", *extra],
            cwd=str(path.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return ReadResult("", "kordoc-failed", {"parse_error": str(exc)})
    if completed.returncode != 0:
        return ReadResult("", "kordoc-failed", {"parse_error": (completed.stderr or completed.stdout or "").strip()[:2000]})
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        return ReadResult("", "kordoc-failed", {"parse_error": "invalid JSON output: %s" % exc})
    if not payload.get("success", False):
        return ReadResult("", "kordoc-failed", {"parse_error": payload.get("error", "kordoc parse failed"), "parse_code": payload.get("code")})
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return ReadResult(
        str(payload.get("markdown") or "").strip(),
        "kordoc-json",
        {
            "kordoc_major_version": major,
            "file_type": payload.get("fileType"),
            "page_count": payload.get("pageCount") or metadata.get("pageCount") or metadata.get("pages") or 1,
            "kordoc_page_quality": payload.get("pageQuality") if isinstance(payload.get("pageQuality"), list) else [],
            "kordoc_quality_summary": payload.get("qualitySummary") if isinstance(payload.get("qualitySummary"), dict) else {},
            "warnings": payload.get("warnings") or [],
        },
    )


def read_document(path: str | Path, *, kordoc_bin: str = "", timeout: int = DEFAULT_TIMEOUT, run: Callable[..., Any] = subprocess.run, which: Callable[[str], str | None] = shutil.which) -> ReadResult:
    """Text of one file. Documents go through Kordoc; text formats are read as UTF-8."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        result = read_with_kordoc(p, kordoc_bin=kordoc_bin, timeout=timeout, run=run, which=which)
        if result is not None:
            return result
        reason = (
            "Kordoc 4.x is required for %s; run `npm install --global kordoc@latest`" % suffix
            if suffix in KORDOC_V4_EXTENSIONS
            else "Kordoc is required for %s; run `npm install --global kordoc@latest`" % suffix
        )
        return ReadResult("", "kordoc-required", {"parse_error": reason})
    return ReadResult(p.read_text(encoding="utf-8", errors="ignore"), "text")


def is_supported(path: Path, extensions: set[str] | frozenset[str]) -> bool:
    return path.is_file() and path.suffix.lower() in extensions
