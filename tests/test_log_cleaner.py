import os
import tempfile
import time
import unittest
from pathlib import Path

from server.log_cleaner import purge_old_logs


class LogCleanerTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_purge_old_logs_deletes_expired_and_preserves_fresh(self):
        now = time.time()
        ten_days_ago = now - (10 * 86400)
        two_days_ago = now - (2 * 86400)

        # 1. Create old log file
        old_file = self.root / "old_run.log"
        old_file.write_text("old log contents", encoding="utf-8")
        os.utime(old_file, (ten_days_ago, ten_days_ago))

        # 2. Create fresh log file
        fresh_file = self.root / "fresh_run.log"
        fresh_file.write_text("fresh log contents", encoding="utf-8")
        os.utime(fresh_file, (two_days_ago, two_days_ago))

        # 3. Create protected file even if old
        protected_file = self.root / "rag_index.json"
        protected_file.write_text("{\"index\": true}", encoding="utf-8")
        os.utime(protected_file, (ten_days_ago, ten_days_ago))

        # 4. Create empty subdirectory with old log
        sub = self.root / "old_sub"
        sub.mkdir()
        sub_old = sub / "sub.log"
        sub_old.write_text("sub log", encoding="utf-8")
        os.utime(sub_old, (ten_days_ago, ten_days_ago))

        deleted, freed = purge_old_logs(self.root, retention_days=7)

        self.assertEqual(deleted, 2)
        self.assertFalse(old_file.exists())
        self.assertFalse(sub_old.exists())
        self.assertFalse(sub.exists())  # Empty sub-dir removed

        self.assertTrue(fresh_file.exists())
        self.assertTrue(protected_file.exists())


if __name__ == "__main__":
    unittest.main()
