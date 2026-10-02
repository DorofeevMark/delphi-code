import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from delphi_code.infrastructure.log_file import PACKAGE_LOGGER, write_logs_to_file


class LogFile(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.root / "logs" / "delphi-code.log"
        root_logger, package_logger = logging.getLogger(), logging.getLogger(PACKAGE_LOGGER)
        handlers, levels = list(root_logger.handlers), (root_logger.level, package_logger.level)
        self.addCleanup(self._restore, handlers, levels)

    def _restore(self, handlers, levels):
        root_logger = logging.getLogger()
        for handler in set(root_logger.handlers) - set(handlers):
            handler.close()
            root_logger.removeHandler(handler)
        root_logger.setLevel(levels[0])
        logging.getLogger(PACKAGE_LOGGER).setLevel(levels[1])

    def configure(self, **environment):
        with patch.dict(os.environ, {"DELPHI_CODE_LOG_FILE": str(self.log), **environment}):
            write_logs_to_file()

    def test_package_messages_reach_the_file_and_third_party_info_does_not(self):
        self.configure()
        logging.getLogger("delphi_code.services.indexing").info("indexed acme/api")
        logging.getLogger("sentence_transformers").info("loading weights")
        logging.getLogger("sentence_transformers").warning("slow tokenizer")
        text = self.log.read_text()
        self.assertIn("INFO delphi_code.services.indexing: indexed acme/api", text)
        self.assertIn("slow tokenizer", text)
        self.assertNotIn("loading weights", text)

    def test_level_controls_package_messages(self):
        self.configure(DELPHI_CODE_LOG_LEVEL="warning")
        logging.getLogger("delphi_code.cli").info("search started")
        self.assertNotIn("search started", self.log.read_text())

    def test_off_writes_nothing(self):
        self.configure(DELPHI_CODE_LOG_LEVEL="OFF")
        self.assertFalse(self.log.exists())

    def test_unwritable_location_disables_logging(self):
        self.log = self.root / "file" / "delphi-code.log"
        (self.root / "file").write_text("")
        self.configure()
        self.assertEqual(
            [
                h
                for h in logging.getLogger().handlers
                if isinstance(h, logging.FileHandler) and Path(h.baseFilename).is_relative_to(self.root)
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
