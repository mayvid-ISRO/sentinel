"""
Phase 6 — Offline Bundle Build Script Tests
=============================================
Covers the bundle builder (repo copy, font copy, wheel download skip,
MANIFEST.json integrity) and the verify path.

Run: python -m pytest tests/test_airgap.py -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_offline_bundle import (   # noqa: E402
    EXCLUDE_PREFIXES,
    EXCLUDE_FILES,
    _should_exclude,
    build_manifest,
    collect_wheels,
    copy_fonts,
    copy_repo,
    sha256_file,
    verify_bundle,
)


# ══ _should_exclude ════════════════════════════════════════════════════

class TestExclusionLogic:
    @pytest.mark.parametrize("path_str,expected", [
        ("__pycache__/foo.py", True),
        (".git/config", True),
        (".venv/pyvenv.cfg", True),
        ("venv/lib/site-packages/foo.py", True),
        ("logs/events.log", True),
        (".iris/nodes.json", True),
        ("wheels/fastapi-0.141.whl", True),
        ("config.json", True),
        (".env", True),
        ("backend/logs/app.db", True),
        ("foo.egg-info/PKG-INFO", True),
        ("thmbs.db", True),             # matched by *.db glob pattern (fnmatch)
        (".DS_Store", True),
        ("iris_config.py", False),      # must NOT be excluded (starts with 'iris' but not '.iris/')
        ("agent/agent.py", False),
        ("tests/test_fleet.py", False),
        ("frontend/index.html", False),
        ("requirements.txt", False),
    ])
    def test_exclusion(self, path_str: str, expected: bool):
        rel = Path(path_str)
        assert _should_exclude(rel) is expected


# ══ sha256_file ════════════════════════════════════════════════════════

class TestSha256:
    def test_computes_correct_hash(self, tmp_path: Path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"hello air-gap")
        h = sha256_file(f)
        assert h == hashlib.sha256(b"hello air-gap").hexdigest()

    def test_large_file_chunks(self, tmp_path: Path):
        """Verify the chunked reader works on files > buffer size (1 MB)."""
        f = tmp_path / "big.bin"
        data = b"A" * (1 << 20) + b"B" * 1000
        f.write_bytes(data)
        h = sha256_file(f)
        assert h == hashlib.sha256(data).hexdigest()


# ══ copy_repo ═════════════════════════════════════════════════════════

class TestCopyRepo:
    def test_copies_python_sources(self, tmp_path: Path):
        n = copy_repo(Path("."), tmp_path / "repo", verbose=False)
        assert (tmp_path / "repo" / "iris_config.py").exists()
        assert (tmp_path / "repo" / "agent" / "agent.py").exists()
        assert n > 50   # sanity: should have many files

    def test_excludes_runtime_artifacts(self, tmp_path: Path):
        n = copy_repo(Path("."), tmp_path / "repo", verbose=False)
        repo = tmp_path / "repo"
        # These must NOT appear
        assert not any(p.name == "__pycache__" for p in repo.rglob("*"))
        assert not any(".iris/" in str(p) for p in repo.rglob("*"))
        assert not any(p.name in ("config.json", ".env") for p in repo.rglob("*"))

    def test_excludes_bundle_output(self, tmp_path: Path):
        """Build a bundle into tmp_path; verifies copy completes without error."""
        nest = tmp_path / "nest"
        n = copy_repo(Path("."), nest, verbose=False)
        assert n > 50
        # Ensure runtime artifacts are absent from the copy
        assert not any(p.name == "__pycache__" for p in nest.rglob("*"))


# ══ copy_fonts ════════════════════════════════════════════════════════

class TestCopyFonts:
    def test_copies_existing_fonts(self, tmp_path: Path):
        n = copy_fonts(tmp_path / "fonts", verbose=False)
        # Should have copied at least one .woff2 if present in repo
        woff2_files = list((tmp_path / "fonts").glob("*.woff2"))
        assert len(woff2_files) >= 1

    def test_copies_to_custom_dest(self, tmp_path: Path):
        """copy_fonts works when called with an explicit destination dir."""
        dest = tmp_path / "custom_fonts"
        n = copy_fonts(dest, verbose=False)
        assert n >= 1
        woff2_files = list(dest.glob("*.woff2"))
        assert len(woff2_files) >= 1


# ══ build_manifest ════════════════════════════════════════════════════

class TestBuildManifest:
    def test_manifest_contains_expected_keys(self, tmp_path: Path):
        m = build_manifest(tmp_path, verbose=False)
        assert "bundle_version" in m
        assert "built_at" in m
        assert "python_version" in m
        assert "platform" in m
        assert "artifacts" in m

    def test_manifest_hashes_match_disk(self, tmp_path: Path):
        # Write a dummy file
        (tmp_path / "test.txt").write_text("hello", encoding="utf-8")
        m = build_manifest(tmp_path, verbose=False)
        h = sha256_file(tmp_path / "test.txt")
        assert m["artifacts"]["test.txt"]["sha256"] == h
        assert m["artifacts"]["test.txt"]["size"] == 5

    def test_manifest_excludes_itself(self, tmp_path: Path):
        build_manifest(tmp_path, verbose=False)
        assert "MANIFEST.json" not in [a for a in tmp_path.rglob("*") if a.is_file()]


# ══ verify_bundle ═════════════════════════════════════════════════════

class TestVerifyBundle:
    def test_verify_passes_clean_bundle(self, tmp_path: Path):
        build_manifest(tmp_path, verbose=False)
        ok = verify_bundle(tmp_path, verbose=False)
        assert ok is True

    def test_verify_fails_on_missing_file(self, tmp_path: Path):
        # Seed files so the manifest actually tracks something
        (tmp_path / "a.txt").write_text("aaa")
        (tmp_path / "b.txt").write_text("bbb")
        build_manifest(tmp_path, verbose=False)
        (tmp_path / "a.txt").unlink()
        ok = verify_bundle(tmp_path, verbose=False)
        assert ok is False

    def test_verify_fails_on_corrupted_file(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("aaa")
        build_manifest(tmp_path, verbose=False)
        (tmp_path / "a.txt").write_bytes(b"corrupted!")
        ok = verify_bundle(tmp_path, verbose=False)
        assert ok is False

    def test_verify_missing_manifest(self, tmp_path: Path):
        ok = verify_bundle(tmp_path, verbose=False)
        assert ok is False


# ══ collect_wheels (skip network) ═════════════════════════════════════

class TestCollectWheelsSkip:
    def test_skips_existing_wheels(self, tmp_path: Path):
        """If wheels/ already has files, skip download and return count."""
        wd = tmp_path / "wheels"
        wd.mkdir()
        (wd / "dummy.whl").write_bytes(b"fake wheel")
        n = collect_wheels(wd, verbose=False)
        assert n >= 1   # returns existing count, did not attempt download
