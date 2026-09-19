#!/usr/bin/env python3
"""
build_offline_bundle.py — Build an air-gapped deployment bundle for IRIS.

Produces iris_offline_bundle/ containing everything needed to run IRIS
on a target machine with zero internet access:

    iris_offline_bundle/
    ├── wheels/               # All pip-downloadable .whl / .tar.gz files
    ├── repo/                 # IRIS source (minus caches, DBs, logs)
    ├── fonts/                # Vendored web fonts (~60 KB)
    ├── MANIFEST.json         # SHA-256 checksums for every artifact
    ├── install.bat           # Windows installer script
    └── install.sh            # Linux/macOS installer script

Usage:
    python scripts/build_offline_bundle.py                    # build to ./iris_offline_bundle/
    python scripts/build_offline_bundle.py --output my-bundle  # custom output dir
    python scripts/build_offline_bundle.py --verify          # verify an existing bundle

The script is idempotent — re-running overwrites artifacts but does not
redo pip downloads that already exist in the output wheels/ directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Paths ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent        # project root
DEFAULT_OUTPUT = REPO_ROOT / "iris_offline_bundle"
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
FONT_DIR = REPO_ROOT / "fonts"          # vendored web fonts at repo root

# Directories/files excluded from the repo copy
EXCLUDE_PREFIXES = (
    ".git", "__pycache__", ".venv", "venv", "*.egg-info",
    "logs", ".iris", "wheels", "iris_offline_bundle",
    "config.json", ".env", "*.db",
)
# Specific files excluded
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
# Windows reserved device names (can appear as files on NTFS but can't be copied normally)
_WIN_RESERVED_NAMES = {"nul", "prn", "aux", "ntp", "con", "lpt1", "lpt2", "lpt3", "com1", "com2", "com3", "com4"}


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ═══════════════════════════════════════════════════════════════════════
# Step 1: Collect wheels
# ═══════════════════════════════════════════════════════════════════════

def collect_wheels(wheels_dir: Path, verbose: bool = True) -> int:
    """Run `pip download -r requirements.txt -d <dir>` and return count."""
    if wheels_dir.exists():
        existing = list(wheels_dir.glob("*"))
        if existing and not any(str(p).endswith(".txt") for p in existing):
            if verbose:
                print(f"  [skip] wheels/ already exists ({len(existing)} files)")
            return len(existing)

    print("  → Downloading wheels …")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "download",
         "-r", str(REQUIREMENTS_FILE),
         "-d", str(wheels_dir),
         "--no-deps"],                   # we pin exact versions; don't pull transitive
        capture_output=True,
        text=True,
        timeout=600,                       # 10 min max for large wheels
    )
    if result.returncode != 0:
        print(f"  ✗ pip download failed:\n{result.stderr[-500:]}", file=sys.stderr)
        sys.exit(1)
    count = sum(1 for _ in wheels_dir.iterdir())
    if verbose:
        print(f"  ✓ {count} wheel/package file(s) downloaded")
    return count


# ═══════════════════════════════════════════════════════════════════════
# Step 2: Copy repo sources
# ═══════════════════════════════════════════════════════════════════════

def _should_exclude(rel: Path) -> bool:
    """Return True if *rel* (relative to the source repo root) should be skipped."""
    # Check every ancestor path segment (parent dirs can match globs like *.egg-info)
    parts: list[Path] = [rel] + list(rel.parents)
    for part in parts:
        name = part.name
        name_lower = name.lower()
        # Windows reserved device names (NTFS allows them as files but they can't be copied)
        if name_lower in _WIN_RESERVED_NAMES:
            return True
        if name in EXCLUDE_FILES:
            return True
        for prefix in EXCLUDE_PREFIXES:
            # Glob-style prefixes (*.egg-info, *.db) — match via pathlib fnmatch
            if "*" in prefix and part.match(prefix):
                return True
            # Plain name: do NOT strip dots from the prefix (keep .env literal)
            # Only strip a leading '*' for glob patterns like *.db
            stem = prefix.lstrip("*")
            if name == stem:
                return True
    return False


def copy_repo(src_repo: Path, dest_repo: Path, verbose: bool = True) -> int:
    """Recursively copy the IRIS repo, skipping caches / runtime artefacts."""
    count = 0
    for root, dirs, files in os.walk(src_repo):
        # Prune excluded directories in-place so os.walk skips them
        dirs[:] = [d for d in dirs if not _should_exclude(Path(root) / d)]
        rel_root = Path(root).relative_to(src_repo)
        dest = dest_repo / rel_root
        dest.mkdir(parents=True, exist_ok=True)
        for fname in files:
            src_file = Path(root) / fname
            dst_file = dest / fname
            if _should_exclude(src_file.relative_to(src_repo)):
                continue
            shutil.copy2(src_file, dst_file)
            count += 1
    if verbose:
        print(f"  ✓ repo copied ({count} files)")
    return count


# ═══════════════════════════════════════════════════════════════════════
# Step 3: Fonts
# ═══════════════════════════════════════════════════════════════════════

def copy_fonts(fonts_dir: Path, verbose: bool = True) -> int:
    """Copy vendored fonts from frontend/fonts/ into the bundle."""
    if not FONT_DIR.exists():
        print("  ⚠ fonts/ not found at frontend/fonts/ — skipping", file=sys.stderr)
        return 0
    dest = fonts_dir
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in FONT_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
            count += 1
    if verbose:
        print(f"  ✓ fonts copied ({count} files)")
    return count


# ═══════════════════════════════════════════════════════════════════════
# Step 4: MANIFEST.json
# ═══════════════════════════════════════════════════════════════════════

def build_manifest(bundle_dir: Path, verbose: bool = True) -> Dict[str, Any]:
    """Walk the bundle tree and produce MANIFEST.json with SHA-256 hashes."""
    manifest: Dict[str, Any] = {
        "bundle_version": "1.0",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "artifacts": {},
    }
    total = 0
    for item in sorted(bundle_dir.rglob("*")):
        if item.name == "MANIFEST.json":
            continue
        rel = item.relative_to(bundle_dir)
        if item.is_file():
            manifest["artifacts"][str(rel)] = {
                "sha256": sha256_file(item),
                "size": item.stat().st_size,
            }
            total += 1
    manifest_dir = bundle_dir
    manifest_path = manifest_dir / "MANIFEST.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    if verbose:
        print(f"  ✓ MANIFEST.json built ({total} artifacts)")
    return manifest


# ═══════════════════════════════════════════════════════════════════════
# Step 5: Installer scripts
# ═══════════════════════════════════════════════════════════════════════

INSTALL_BAT_TEMPLATE = r"""@echo off
REM ──────────────────────────────────────────────────────────────
REM IRIS Offline Bundle Installer — Windows
REM Extract this archive on the target machine, then double-click.
REM No internet access required after extraction.
REM ──────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion
set "BUNDLE_DIR=%~dp0"
set "PYTHON=%PythonDir%\python.exe"

echo ============================================================
echo  IRIS Air-Gapped Installer (Windows)
echo ============================================================
echo.

REM Locate Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: python not found in PATH.
    echo Please install Python 3.12+ first, then re-run this script.
    echo See: https://www.python.org/downloads/
    pause
    exit /b 1
)
set "PYTHON=python"

echo [1/4] Verifying bundle integrity …
%PYTHON% -c "import json,hashlib,sys; m=json.load(open(r'%BUNDLE_DIR%MANIFEST.json')); errors=[]; [errors.append(f'{k}: MISSING') if not __import__('pathlib').Path(r'%BUNDLE_DIR%'+k).exists() else None for k in m['artifacts']]; [errors.append(f'{k}: HASH MISMATCH') for k,v in m['artifacts'].items() if __import__('pathlib').Path(r'%BUNDLE_DIR%'+k).exists() and hashlib.sha256(__import__('pathlib').Path(r'%BUNDLE_DIR%'+k).read_bytes()).hexdigest()!=v['sha256']]; [print('  OK',k) for k in m['artifacts']]; sys.exit(1) if errors else None" || (
    echo.
    echo ERROR: Bundle integrity check FAILED! Do not proceed.
    echo The bundle may have been corrupted during transfer.
    pause
    exit /b 1
)
echo   Integrity verified.
echo.

echo [2/4] Creating virtual environment …
%PYTHON% -m venv "%BUNDLE_DIR%.venv"
if %errorlevel% neq 0 (
    echo ERROR: venv creation failed.
    pause; exit /b 1
)
echo   Virtual environment ready.
echo.

echo [3/4] Installing packages (offline) …
"%BUNDLE_DIR%.venv\Scripts\pip.exe" install --no-index --find-links "%BUNDLE_DIR%\wheels" -r "%BUNDLE_DIR%\repo\requirements.txt"
if %errorlevel% neq 0 (
    echo ERROR: package installation failed.
    pause; exit /b 1
)
echo   Packages installed.
echo.

echo [4/4] Cleaning up temporary files …
del "%BUNDLE_DIR%\__MACOSX" /s /f /q 2>nul
echo.

echo ============================================================
echo  INSTALLATION COMPLETE
echo ============================================================
echo.
echo Next steps:
echo   cd %BUNDLE_DIR%\repo
echo   copy config.example.json config.json
echo   edit config.json  ^<— set llm.url to your Ollama host
echo   python -m uvicorn backend.main:app --port 8000
echo.
pause
"""

INSTALL_SH_TEMPLATE = r"""#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# IRIS Offline Bundle Installer — Linux / macOS
# Extract this archive on the target machine, then run:
#   chmod +x install.sh && ./install.sh
# No internet access required after extraction.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

echo "============================================================"
echo " IRIS Air-Gapped Installer (Linux/macOS)"
echo "============================================================"
echo ""

# ── Pre-flight: python available? ─────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found in PATH."
    echo "Please install Python 3.12+ first:"
    echo "  Ubuntu/Debian : sudo apt install python3.12 python3.12-venv"
    echo "  RHEL/Fedora   : sudo dnf install python3.12"
    echo "  macOS         : brew install python@3.12"
    exit 1
fi

echo "[1/4] Verifying bundle integrity …"
"$PYTHON" - <<'PY'
import json, hashlib, sys
from pathlib import Path
bundle = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
m = json.load(open(bundle / "MANIFEST.json"))
errors = []
for k, v in m["artifacts"].items():
    p = bundle / k
    if not p.exists():
        errors.append(f"{k}: MISSING")
    elif hashlib.sha256(p.read_bytes()).hexdigest() != v["sha256"]:
        errors.append(f"{k}: HASH MISMATCH")
for e in errors:
    print(f"  ✗ {e}")
sys.exit(1) if errors else print("  OK — all hashes match.")
PY "$BUNDLE_DIR" || { echo "ERROR: Integrity check FAILED."; exit 1; }
echo ""

echo "[2/4] Creating virtual environment …"
"$PYTHON" -m venv "$BUNDLE_DIR/.venv"
echo "  Virtual environment ready."
echo ""

echo "[3/4] Installing packages (offline) …"
"$BUNDLE_DIR/.venv/bin/pip" install --no-index --find-links="$BUNDLE_DIR/wheels" \
    -r "$BUNDLE_DIR/repo/requirements.txt"
echo "  Packages installed."
echo ""

echo "[4/4] Cleaning up …"
rm -rf "$BUNDLE_DIR/__MACOSX" 2>/dev/null || true
echo ""

echo "============================================================"
echo " INSTALLATION COMPLETE"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  cd $BUNDLE_DIR/repo"
echo "  cp config.example.json config.json"
echo "  # edit config.json — set llm.url to your Ollama host"
echo "  python -m uvicorn backend.main:app --port 8000"
echo ""
"""


def write_installers(bundle_dir: Path, verbose: bool = True) -> None:
    bat = bundle_dir / "install.bat"
    sh = bundle_dir / "install.sh"
    bat.write_text(INSTALL_BAT_TEMPLATE, encoding="utf-8")
    sh.write_text(INSTALL_SH_TEMPLATE, encoding="utf-8")
    sh.chmod(0o755)
    if verbose:
        print(f"  ✓ installer scripts written ({bat.name}, {sh.name})")


# ═══════════════════════════════════════════════════════════════════════
# Main orchestration
# ═══════════════════════════════════════════════════════════════════════

def build_bundle(output_dir: Path, verbose: bool = True) -> Dict[str, Any]:
    """Build the complete offline bundle and return summary stats."""
    output_dir.mkdir(parents=True, exist_ok=True)
    wheels_dir = output_dir / "wheels"
    repo_dir = output_dir / "repo"
    fonts_dir = output_dir / "fonts"

    if verbose:
        print(f"\n{'='*60}")
        print(f" Building IRIS Offline Bundle")
        print(f" Output : {output_dir}")
        print(f" Python : {sys.version.split()[0]} ({sys.platform})")
        print(f"{'='*60}\n")

    n_wheels   = collect_wheels(wheels_dir, verbose)
    n_repo     = copy_repo(REPO_ROOT, repo_dir, verbose)
    n_fonts    = copy_fonts(fonts_dir, verbose)
    manifest   = build_manifest(output_dir, verbose)
    write_installers(output_dir, verbose)

    size_mb = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file()) / (1 << 20)
    if verbose:
        print(f"\n{'='*60}")
        print(f" Bundle ready: {output_dir.resolve()}")
        print(f"  {n_wheels} wheel/package files")
        print(f"  {n_repo} repository files")
        print(f"  {n_fonts} font files")
        print(f"  Total size : ~{size_mb:.1f} MB")
        print(f"{'='*60}\n")

    return {
        "output_dir": str(output_dir.resolve()),
        "wheels": n_wheels,
        "repo_files": n_repo,
        "fonts": n_fonts,
        "size_mb": round(size_mb, 1),
        "manifest_artifacts": len(manifest["artifacts"]),
    }


def verify_bundle(bundle_dir: Path, verbose: bool = True) -> bool:
    """Verify integrity of an existing bundle against its MANIFEST.json."""
    manifest_path = bundle_dir / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"  ✗ No MANIFEST.json found in {bundle_dir}", file=sys.stderr)
        return False

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    errors: List[str] = []
    for rel, info in manifest["artifacts"].items():
        p = bundle_dir / rel
        if not p.exists():
            errors.append(f"  ✗ {rel}: MISSING")
            continue
        actual = sha256_file(p)
        if actual != info["sha256"]:
            errors.append(f"  ✗ {rel}: HASH MISMATCH (expected={info['sha256'][:12]}… got={actual[:12]}…)")
        else:
            if verbose:
                print(f"  ✓ {rel}")

    ok = not errors
    if ok:
        print(f"\n  ✓ Bundle verified OK ({len(manifest['artifacts'])} artifacts)\n")
    else:
        print(f"\n  ✗ {len(errors)} integrity error(s) found\n")
        for e in errors[:10]:
            print(e)
        if len(errors) > 10:
            print(f"  … and {len(errors) - 10} more")
    return ok


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build or verify an air-gapped IRIS deployment bundle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT),
        help=f"Bundle output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--verify", "-v",
        action="store_true",
        help="Verify an existing bundle instead of building it",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    bundle = Path(args.output).resolve()

    if args.verify:
        ok = verify_bundle(bundle, verbose=not args.quiet)
        sys.exit(0 if ok else 1)
    else:
        stats = build_bundle(bundle, verbose=not args.quiet)
        print("\nDone. Transfer the bundle directory to the air-gapped host.")
        print(f"  Size: ~{stats['size_mb']} MB  Artifacts: {stats['manifest_artifacts']}")
        sys.exit(0)


if __name__ == "__main__":
    main()
