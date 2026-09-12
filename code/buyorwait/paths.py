"""Dataset and output path resolution (PLAN.md 5.8).

Paths resolve relative to this file rather than to the current working directory, so
``python code/main.py`` behaves identically from the repository root, from inside ``code/``,
and from an extracted ``code.zip`` sitting beside a ``dataset/`` directory.
"""

from __future__ import annotations

import os
from pathlib import Path

DATASET_FILES = (
    "financial_profiles.csv",
    "financial_events.csv",
    "exchange_rates.csv",
    "requests.csv",
    "sample_requests.csv",
    "request_payment_options.csv",
    "messages.csv",
    "images.csv",
)

# code/buyorwait/paths.py -> code/buyorwait -> code -> <root>
_CODE_DIR = Path(__file__).resolve().parent.parent
_SEARCH_ROOTS = (_CODE_DIR, _CODE_DIR.parent, _CODE_DIR.parent.parent)


def _looks_like_dataset(candidate: Path) -> bool:
    """A directory is the dataset only if it carries the files the pipeline reads."""
    return all((candidate / name).is_file() for name in DATASET_FILES)


def find_dataset(override: str | os.PathLike[str] | None = None) -> Path:
    """Locate ``dataset/``.

    An explicit override is honoured as given and validated, so a wrong ``--dataset`` fails
    loudly instead of falling back to a directory the caller did not ask for.
    """
    if override is not None:
        path = Path(override).expanduser().resolve()
        if not _looks_like_dataset(path):
            missing = [n for n in DATASET_FILES if not (path / n).is_file()]
            raise FileNotFoundError(f"{path} is not a dataset directory; missing: {', '.join(missing)}")
        return path

    for root in _SEARCH_ROOTS:
        candidate = root / "dataset"
        if _looks_like_dataset(candidate):
            return candidate
    searched = ", ".join(str(r) for r in _SEARCH_ROOTS)
    raise FileNotFoundError(f"could not locate a dataset/ directory; searched under: {searched}")


def default_output(dataset: Path) -> Path:
    """``output.csv`` beside the dataset directory, i.e. at the project root."""
    return dataset.parent / "output.csv"


def code_dir() -> Path:
    return _CODE_DIR


def cache_dir() -> Path:
    return _CODE_DIR.parent / ".cache"
