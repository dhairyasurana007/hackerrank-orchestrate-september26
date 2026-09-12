"""Path resolution must not depend on the working directory (PLAN.md 5.8, TASKS.md M13)."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from buyorwait import paths


class TestDatasetResolution(unittest.TestCase):
    def test_finds_the_real_dataset_regardless_of_cwd(self):
        expected = paths.find_dataset()
        for cwd in (paths.code_dir(), paths.code_dir().parent, tempfile.gettempdir()):
            previous = Path.cwd()
            os.chdir(cwd)
            try:
                self.assertEqual(paths.find_dataset(), expected)
            finally:
                os.chdir(previous)

    def test_output_goes_beside_the_dataset_directory(self):
        dataset = paths.find_dataset()
        self.assertEqual(paths.default_output(dataset), dataset.parent / "output.csv")

    def test_a_wrong_override_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                paths.find_dataset(tmp)

    def test_an_extracted_archive_layout_resolves(self):
        """`code.zip` extracted beside a `dataset/` copy: the layout the grader will run."""
        real = paths.find_dataset()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "dataset").mkdir()
            for name in paths.DATASET_FILES:
                shutil.copyfile(real / name, root / "dataset" / name)
            self.assertTrue(paths._looks_like_dataset(root / "dataset"))
            self.assertEqual(paths.find_dataset(root / "dataset"), (root / "dataset").resolve())


if __name__ == "__main__":
    unittest.main()
