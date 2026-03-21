import tempfile
import unittest
from pathlib import Path

from job_tracker.models import JobPosting
from job_tracker.storage import load_previous_snapshot, save_snapshot


class StorageTests(unittest.TestCase):
    def test_load_missing_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "seen_jobs.json"
            self.assertEqual(load_previous_snapshot(state_path), {})

    def test_save_and_load_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "seen_jobs.json"
            save_snapshot(
                state_path,
                [
                    JobPosting(
                        job_id="12",
                        title="Data Analyst",
                        url="https://example.com/12",
                        organization="Org",
                        location="Remote",
                    )
                ],
            )
            loaded = load_previous_snapshot(state_path)
            self.assertEqual(set(loaded.keys()), {"12"})
            self.assertEqual(loaded["12"].title, "Data Analyst")


if __name__ == "__main__":
    unittest.main()
