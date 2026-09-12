#!/usr/bin/env python3
"""Build and verify the HackerRank submission archive."""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


CODE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_DIR.parent
DEFAULT_ZIP = REPO_ROOT / "code.zip"
REQUIRED_MEMBERS = {"main.py", "buyorwait/__init__.py", "evaluation/usage_report.md"}
FORBIDDEN_NAMES = {".env", "log.txt"}
FORBIDDEN_PARTS = {"__pycache__", ".cache"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_MEMBERS = {"evaluation/model_run_log.jsonl"}


def should_include(path: Path) -> bool:
    relative = path.relative_to(CODE_DIR)
    parts = set(relative.parts)
    if parts & FORBIDDEN_PARTS:
        return False
    if path.name in FORBIDDEN_NAMES:
        return False
    if path.suffix in FORBIDDEN_SUFFIXES:
        return False
    if relative.as_posix() in FORBIDDEN_MEMBERS:
        return False
    return path.is_file()


def build_archive(target: Path) -> None:
    target = target.resolve()
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(CODE_DIR.rglob("*")):
            if should_include(path):
                archive.write(path, path.relative_to(CODE_DIR).as_posix())


def verify_layout(target: Path) -> None:
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
    missing = sorted(REQUIRED_MEMBERS - names)
    if missing:
        raise SystemExit(f"archive missing required member(s): {', '.join(missing)}")
    forbidden = [
        name
        for name in names
        if name in FORBIDDEN_MEMBERS
        or Path(name).name in FORBIDDEN_NAMES
        or any(part in FORBIDDEN_PARTS for part in Path(name).parts)
        or Path(name).suffix in FORBIDDEN_SUFFIXES
    ]
    if forbidden:
        raise SystemExit(f"archive contains forbidden member(s): {', '.join(sorted(forbidden))}")


def verify_run(target: Path, dataset: Path, expected_output: Path, *, no_llm: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="buy-or-wait-package-") as tmp:
        root = Path(tmp)
        extracted_code = root / "code"
        extracted_code.mkdir()
        with zipfile.ZipFile(target) as archive:
            archive.extractall(extracted_code)
        shutil.copytree(dataset, root / "dataset")
        produced = root / "output.csv"
        command = [
            sys.executable,
            str(extracted_code / "main.py"),
            "--dataset",
            str(root / "dataset"),
            "--output",
            str(produced),
        ]
        if no_llm:
            command.append("--no-llm")
        subprocess.run(command, cwd=root, check=True)
        if not filecmp.cmp(expected_output, produced, shallow=False):
            raise SystemExit("clean archive run did not reproduce the expected output.csv")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="archive path to build")
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "dataset")
    parser.add_argument("--expected-output", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--verify-run", action="store_true", help="extract and reproduce output.csv")
    parser.add_argument("--no-llm", action="store_true", help="use --no-llm during --verify-run")
    args = parser.parse_args(argv)

    build_archive(args.zip)
    verify_layout(args.zip)
    if args.verify_run:
        verify_run(args.zip, args.dataset, args.expected_output, no_llm=args.no_llm)
    print(f"wrote {args.zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
