"""Kordoc 4.x document routing for LocalCrab ingest."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from opencrab.ontology import documents
from opencrab.ontology.documents import read_document


def fake_kordoc(version: str, payload: dict | None = None, returncode: int = 0):
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(list(command))
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout=version, stderr="")
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload or {}), stderr="boom" if returncode else "")

    return run, calls


def setup_function() -> None:
    documents._MAJOR_CACHE.clear()


def test_hwpx_is_parsed_with_kordoc_4_without_images(tmp_path: Path) -> None:
    doc = tmp_path / "report.hwpx"
    doc.write_bytes(b"PK")
    run, calls = fake_kordoc("kordoc 4.18.13", {"success": True, "markdown": "# 청년 쉬었음\n\n본문", "fileType": "hwpx", "pageCount": 3})
    result = read_document(doc, run=run, which=lambda name: "/usr/local/bin/kordoc")
    assert result.ok and result.parser == "kordoc-json"
    assert "청년 쉬었음" in result.text
    assert result.meta["page_count"] == 3 and result.meta["kordoc_major_version"] == 4
    parse = calls[-1]
    assert parse[:2] == ["/usr/local/bin/kordoc", str(doc)] and "--format" in parse and "--no-images" in parse


def test_kordoc_3_is_not_used_for_v4_formats(tmp_path: Path) -> None:
    doc = tmp_path / "a.docx"
    doc.write_bytes(b"PK")
    run, calls = fake_kordoc("3.2.0")
    result = read_document(doc, run=run, which=lambda name: "/bin/kordoc")
    assert not result.ok and result.parser == "kordoc-required"
    assert "4.x" in result.meta["parse_error"]
    assert all(call[-1] == "--version" for call in calls)


def test_pdf_with_kordoc_3_still_parses_without_the_image_flag(tmp_path: Path) -> None:
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"%PDF")
    run, calls = fake_kordoc("3.2.0", {"success": True, "markdown": "text"})
    assert read_document(doc, run=run, which=lambda name: "/bin/kordoc").ok
    assert "--no-images" not in calls[-1]


def test_missing_kordoc_and_parse_failure_are_reported(tmp_path: Path) -> None:
    doc = tmp_path / "a.hwp"
    doc.write_bytes(b"x")
    missing = read_document(doc, run=lambda *a, **k: None, which=lambda name: None)
    assert missing.parser == "kordoc-required" and "npm install" in missing.meta["parse_error"]
    run, _ = fake_kordoc("4.1.0", returncode=2)
    failed = read_document(doc, run=run, which=lambda name: "/bin/kordoc")
    assert failed.parser == "kordoc-failed" and failed.meta["parse_error"] == "boom"


def test_text_files_are_read_directly(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# 메모", encoding="utf-8")
    result = read_document(note, which=lambda name: None)
    assert result.ok and result.parser == "text" and result.text == "# 메모"


def test_kordoc_bin_setting_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KORDOC_BIN", "npx kordoc")
    assert documents.resolve_kordoc_command(which=lambda name: "/bin/kordoc") == ["npx", "kordoc"]
    assert documents.resolve_kordoc_command("/opt/k", which=lambda name: None) == ["/opt/k"]
