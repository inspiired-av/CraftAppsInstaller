"""Portable unit tests plus real Windows integration coverage for Shell Links."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import windows_native as win


class WindowsNativeTests(unittest.TestCase):
    def test_known_guids_have_valid_structure(self):
        guid = win._GUID.parse("00021401-0000-0000-C000-000000000046")
        self.assertEqual(guid.Data1, 0x00021401)
        self.assertEqual(guid.Data4[0], 0xC0)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only shortcut input test")
    def test_rejects_invalid_windows_shortcut_names(self):
        with patch.object(win, "_require_windows"):
            path = Path(sys.executable)
            for name in ("", "..", "app/name", "app:name", "bad?", "trailing."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    win.create_shortcut(path, name)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only COM integration test")
    def test_create_shortcut_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(win, "known_folder", return_value=Path(temp)):
                shortcut = win.create_shortcut(Path(sys.executable), "CraftAppsTester")
                self.assertTrue(shortcut.is_file())
                self.assertEqual(str(win.shortcut_target(shortcut)).casefold(),
                                 str(Path(sys.executable)).casefold())

    @unittest.skipUnless(sys.platform == "win32", "Windows-only version-resource test")
    def test_python_executable_version(self):
        version = win.executable_version(Path(sys.executable))
        self.assertIsNotNone(version)
        self.assertTrue(version[0].isdigit())

    @unittest.skipUnless(sys.platform == "win32", "Windows-only registry lookup test")
    def test_registered_executable_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "photocraft.exe"
            exe.touch()
            with patch.object(win, "_registered_executables", return_value=iter([exe])):
                self.assertEqual(
                    win.find_installed_executable("photocraft", "PhotoCraft"), exe.resolve())


if __name__ == "__main__":
    unittest.main()
