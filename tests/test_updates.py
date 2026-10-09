import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import update_support as u


class UpdateSupportTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(u.has_update("v0.2.0", "v0.3.0"))
        self.assertFalse(u.has_update("v0.3.0", "v0.3.0"))
        self.assertFalse(u.has_update("v0.4.0", "v0.3.0"))
        self.assertIsNone(u.has_update(None, "v0.3.0"))
        self.assertTrue(u.has_update("v1.0.0-rc1", "v1.0.0"))

    def test_version_parts(self):
        self.assertEqual(u.version_parts("1:0.3.2-1"), (0, 3, 2, 0))
        self.assertEqual(u.version_parts("v10.2"), (10, 2, 0, 0))

    def test_receipt_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "installed.json"
            with patch.object(u, "state_file", return_value=path):
                u.remember_install("gridcraft", "v1.2.3", "Linux", "x64",
                                   "gridcraft-linux-x86_64.AppImage", location=temp,
                                   linux_format="AppImage", standalone=True)
                data = u.read_receipts("Linux")
                self.assertEqual(data["gridcraft"]["version"], "v1.2.3")
                self.assertTrue(data["gridcraft"]["standalone"])

    def test_missing_receipt_does_not_create_fake_install(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(u, "state_file", return_value=Path(temp) / "missing.json"), \
                 patch.object(u, "_linux_installed", return_value=None):
                self.assertIsNone(u.detect_installed("gridcraft", "Linux"))

    def test_native_version_overrides_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(u, "state_file", return_value=Path(temp) / "missing.json"), \
                 patch.object(u, "_linux_installed", return_value={
                     "version": "v0.4.0", "source": "dpkg", "linux_format": "DEB",
                     "standalone": False, "location": None}):
                self.assertEqual(u.detect_installed("gridcraft", "Linux")["version"], "v0.4.0")

    def test_macos_bundle_version(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "PhotoCraft.app"
            plist = bundle / "Contents" / "Info.plist"
            plist.parent.mkdir(parents=True)
            plist.write_bytes(
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
                b'<plist version="1.0"><dict>'
                b'<key>CFBundleShortVersionString</key><string>0.8.0</string>'
                b'</dict></plist>'
            )
            with patch.object(u.Path, "home", return_value=Path(temp)), \
                 patch.object(u, "_command", return_value=None):
                found = u._mac_installed("photocraft")
                # macOS seeks ~/Applications by design, not arbitrary folders.
                self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
