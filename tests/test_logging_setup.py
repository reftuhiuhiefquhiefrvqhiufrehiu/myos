import logging
import sys
import tempfile
import unittest
from pathlib import Path

import logging_setup


class LoggingSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_hook = sys.excepthook
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.logger = logging_setup.setup_logging(
            log_dir=self._tmp.name, level=logging.DEBUG, force=True
        )
        self.addCleanup(self._teardown_logging)

    def _teardown_logging(self) -> None:
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        if hasattr(self.logger, "_neonveil_configured"):
            delattr(self.logger, "_neonveil_configured")
        sys.excepthook = self._original_hook

    def test_setup_creates_log_file_and_writes_messages(self) -> None:
        self.logger.info("Hallo NeonVeil")
        for handler in self.logger.handlers:
            handler.flush()
        log_file = Path(self._tmp.name) / logging_setup.LOG_FILE_NAME
        self.assertTrue(log_file.exists())
        self.assertIn("Hallo NeonVeil", log_file.read_text(encoding="utf-8"))

    def test_exception_hook_logs_and_notifies(self) -> None:
        captured = []
        logging_setup.install_exception_hook(
            on_error=lambda *args: captured.append(args),
            logger=self.logger,
        )
        try:
            raise ValueError("kaputt")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        for handler in self.logger.handlers:
            handler.flush()
        log_file = Path(self._tmp.name) / logging_setup.LOG_FILE_NAME
        self.assertIn("Unbehandelte Ausnahme", log_file.read_text(encoding="utf-8"))
        self.assertEqual(len(captured), 1)

    def test_keyboard_interrupt_is_delegated(self) -> None:
        delegated = []
        original = sys.__excepthook__
        sys.__excepthook__ = lambda *args: delegated.append(args)
        self.addCleanup(setattr, sys, "__excepthook__", original)

        hook = logging_setup.install_exception_hook(logger=self.logger)
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            hook(*sys.exc_info())
        self.assertEqual(len(delegated), 1)


if __name__ == "__main__":
    unittest.main()
