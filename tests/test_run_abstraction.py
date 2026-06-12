from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from run_abstraction import local_pdf_paths


class RunAbstractionTests(unittest.TestCase):
    def test_local_pdf_paths_returns_sorted_pdfs_only(self) -> None:
        with TemporaryDirectory() as directory:
            input_dir = Path(directory)
            second = input_dir / "b.pdf"
            first = input_dir / "a.PDF"
            ignored = input_dir / "notes.txt"
            second.write_bytes(b"%PDF-second")
            first.write_bytes(b"%PDF-first")
            ignored.write_text("not a PDF", encoding="utf-8")

            self.assertEqual(
                local_pdf_paths(input_dir),
                [first.resolve(), second.resolve()],
            )


if __name__ == "__main__":
    unittest.main()
