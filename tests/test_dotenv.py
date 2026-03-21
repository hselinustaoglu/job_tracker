import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_tracker.notifications import _load_dotenv


class DotenvTests(unittest.TestCase):
    def test_loads_values_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "SMTP_HOST=smtp.gmail.com\nALERT_TO_EMAIL=test@example.com\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                _load_dotenv(str(dotenv_path))
                self.assertEqual(os.environ["SMTP_HOST"], "smtp.gmail.com")
                self.assertEqual(os.environ["ALERT_TO_EMAIL"], "test@example.com")


if __name__ == "__main__":
    unittest.main()
