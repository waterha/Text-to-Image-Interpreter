"""Offline regressions for long-running worker recovery; no real model requests."""
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import win32api
import win32con
import win32gui

from til import diagnostics
from til.tray import Tray


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.log = logging.getLogger("TIL.runtime-test")
        self.log.handlers = [logging.NullHandler()]
        self.log.propagate = False
        self.logging_patch = patch.object(diagnostics, "_LOGGER", self.log)
        self.logging_patch.start()
        self.addCleanup(self.logging_patch.stop)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.01)
        return bool(predicate())

    def test_hotkey_survives_tray_restart(self):
        events = []
        tray = Tray(lambda *event: events.append(event))
        register = win32gui.RegisterHotKey
        # Reserve an unused test combination, not the user's Alt+Q.
        def test_register(hwnd, identity, modifiers, key):
            return register(hwnd, identity, win32con.MOD_CONTROL | win32con.MOD_ALT | win32con.MOD_SHIFT, win32con.VK_F24)
        try:
            with patch.object(tray, "add_icon"), patch("til.tray._icon_handle", return_value=0), \
                 patch("til.tray.win32gui.Shell_NotifyIcon"), \
                 patch("til.tray.win32gui.RegisterHotKey", side_effect=test_register):
                tray.start()
                self.assertTrue(self.wait_for(lambda: tray.hwnd is not None), events)
                self.assertTrue(self.wait_for(lambda: tray.hotkey_registered), "Successful RegisterHotKey returns None, not a bool")
                for cycle in range(5):
                    win32gui.PostMessage(tray.hwnd, win32con.WM_HOTKEY, 1, 0)
                    self.assertTrue(self.wait_for(lambda: events.count(("hotkey", None)) == cycle + 1), events)
                    win32gui.PostMessage(tray.hwnd, win32con.WM_CLOSE, 0, 0)
                    tray.thread.join(2)
                    self.assertFalse(tray.thread.is_alive())
                    self.assertIsNone(tray.hwnd)
                    tray.restart_after = 0
                    tray.ensure_running()
                    self.assertTrue(self.wait_for(lambda: tray.hwnd is not None and tray.hotkey_registered), events)
                self.assertFalse(any(kind == "notice" for kind, _ in events), events)
        finally:
            tray.stop()
            tray.thread.join(2)
            # Cleanup also on the old version that leaks a class registration.
            try:
                import os
                win32gui.UnregisterClass(f"TILTray{os.getpid()}", win32api.GetModuleHandle(None))
            except Exception:
                pass

    def test_exception_log_does_not_include_private_values(self):
        import io
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        self.log.handlers = [handler]
        diagnostics.exception("test exception", ValueError("private selected text SECRET_KEY"))
        self.assertIn("ValueError", stream.getvalue())
        self.assertNotIn("private selected", stream.getvalue())
        self.assertNotIn("SECRET_KEY", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
