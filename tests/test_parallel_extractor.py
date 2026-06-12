from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import extractor
from src.schemas import AttributeSpec


class _ConcurrencyTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.client_threads: set[int] = set()

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.client_threads.add(threading.get_ident())

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class _FakeClient:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self.tracker = tracker
        self.last_response_text = None

    def complete_json(self, prompt: str) -> dict:
        self.tracker.enter()
        try:
            time.sleep(0.04)
            return {
                "is_enumerate": False,
                "single_value": {
                    "fields": {"Value": prompt},
                    "confidence_score": 90,
                    "confidence_level": "high",
                    "source": {
                        "document_id": "doc_001",
                        "file_name": "lease.pdf",
                        "document_type": "Base",
                        "page_number": 1,
                        "source_clause": "supporting text",
                    },
                    "trace": [],
                },
            }
        finally:
            self.tracker.leave()


class ParallelExtractorTests(unittest.TestCase):
    def test_runs_four_llm_calls_in_parallel_and_preserves_result_indexes(self) -> None:
        tracker = _ConcurrencyTracker()
        extractor._LLM_THREAD_LOCAL = threading.local()
        spec = AttributeSpec(
            attribute_key="test_attribute",
            display_name="Test Attribute",
            category="Test",
        )
        retrieved = [
            {
                "block": {
                    "file_name": "lease.pdf",
                    "page_number": 1,
                    "text": "supporting text",
                }
            }
        ]

        with TemporaryDirectory() as temp_dir:
            tasks = [
                {
                    "index": index,
                    "spec": spec,
                    "retrieved": retrieved,
                    "prompt": f"value-{index}",
                    "intermediate_dir": Path(temp_dir),
                    "dry_run": False,
                }
                for index in range(1, 9)
            ]
            with patch.object(
                extractor,
                "build_llm_client",
                side_effect=lambda **_: _FakeClient(tracker),
            ):
                results = extractor._run_parallel_extraction(tasks, max_workers=4)

        self.assertEqual(sorted(results), list(range(1, 9)))
        self.assertEqual(tracker.maximum, 4)
        self.assertLessEqual(len(tracker.client_threads), 4)


if __name__ == "__main__":
    unittest.main()
