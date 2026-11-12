"""Tests for stage 0 ingest."""

from __future__ import annotations

import json
from pathlib import Path

from checkier.config import PipelineConfig
from checkier.stage0_ingest import run_ingest


def _make_data(tmp_path: Path, files: dict[str, tuple[str, str]]) -> Path:
    """files: {sub/filename.md: (subdir, content)}"""
    data = tmp_path / "data_md"
    for rel, (sub, content) in files.items():
        d = data / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(content, encoding="utf-8")
    return data


def test_ingest_basic(tmp_path):
    files = {
        "a.md": ("данные", "hello"),
        "b.md": ("card", "world"),
        "c.md": ("request", "scope"),
    }
    data = _make_data(
        tmp_path,
        {name: (sub, content) for name, (sub, content) in files.items()},
    )
    cfg = PipelineConfig(data_dir=data, out_dir=tmp_path / "out")
    mapping = run_ingest(cfg)
    assert len(mapping) == 3
    for h8, entry in mapping.items():
        assert (cfg.files_dir / f"{h8}.md").exists()
        assert entry["role"] in ("data", "card", "request")
    roles = {e["role"] for e in mapping.values()}
    assert roles == {"data", "card", "request"}


def test_ingest_detects_duplicates(tmp_path):
    content = "same content"
    data = _make_data(
        tmp_path,
        {
            "f1.md": ("данные", content),
            "f2.md": ("данные", content),
            "f3.md": ("данные", "different"),
        },
    )
    cfg = PipelineConfig(data_dir=data, out_dir=tmp_path / "out")
    mapping = run_ingest(cfg)
    dups = {h8: e for h8, e in mapping.items() if e.get("duplicate_of")}
    assert len(dups) == 1
    dup = next(iter(dups.values()))
    assert dup["tag"] == "dup"
    assert dup["duplicate_of"] in mapping


def test_ingest_collision_resample(tmp_path, monkeypatch):
    # force a name collision by controlling secrets.token_hex
    import secrets

    calls = iter(["deadbeef", "cafebabe"])

    def fake_token_hex(n):
        return next(calls)

    monkeypatch.setattr(secrets, "token_hex", fake_token_hex)
    files = {
        "a.md": ("данные", "one"),
        "b.md": ("данные", "two"),
    }
    data = _make_data(tmp_path, files)
    cfg = PipelineConfig(data_dir=data, out_dir=tmp_path / "out")
    mapping = run_ingest(cfg)
    assert "deadbeef" in mapping
    assert "cafebabe" in mapping
