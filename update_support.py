"""Local installed-version discovery and receipts for Craft Apps Installer.

No third-party dependencies. Only known Craft application IDs are probed.
Unknown versions are *not* considered out of date automatically.
"""
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

KNOWN_APPS = frozenset((
    "photocraft", "lightcraft", "filmcraft", "effectcraft", "designcraft",
    "pdfcraft", "vectorcraft", "wordcraft", "gridcraft", "deckcraft"
))


def app_name(app):
    if app not in KNOWN_APPS:
        raise ValueError("Unknown Craft application")
    return app[:-5].capitalize() + "Craft"


def version_parts(text):
    """Numeric component of releases such as v0.3.0 or 1:0.3.0-1."""
    if not text:
        return None
    match = re.search(r"(?<![0-9])([0-9]+)\.([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?(?![0-9])", str(text))
    if not match:
        return None
    numbers = tuple(int(part or 0) for part in match.groups())
    return numbers


def has_update(installed_version, latest_version):
    """True only for provably newer versions; None if comparison is unsafe."""
    old = version_parts(installed_version)
    new = version_parts(latest_version)
    if old is None or new is None:
        return None
    if new > old:
        return True
    # Stable 1.0.0 supersedes 1.0.0-rc1 (but not a newer numeric version).
    return bool(new == old and re.search(r"(?i)(?:alpha|beta|preview|rc[0-9]|dev)", str(installed_version)))


def state_file(system=None):
    system = system or platform.system()
    if system in ("Windows",):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif system in ("macOS", "Darwin"):
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "CraftAppsInstaller" / "installed.json"


def read_receipts(system=None):
    try:
        data = json.loads(state_file(system).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def remember_install(app, version, system, arch, asset_name, location=None,
                     linux_format=None, standalone=False, desktop=False, shortcut_label=None):
    """Record only completed installations, never download-only operations."""
    app_name(app)
    path = state_file(system)
    receipts = read_receipts(system)
    receipts[app] = {
        "version": version, "system": system, "arch": arch,
        "asset_name": asset_name, "location": str(location) if location else None,
        "linux_format": linux_format if system == "Linux" else None,
        "standalone": bool(standalone), "desktop": bool(desktop),
        "shortcut_label": shortcut_label if desktop else None
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".installed-", suffix=".tmp", delete=False) as f:
            temp = Path(f.name)
            json.dump(receipts, f, indent=2, sort_keys=True)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _command(args, timeout=5):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _windows_registry(app):
    try:
        import winreg
    except ImportError:
        return None
    name = app_name(app).casefold()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as sub:
                                title = str(winreg.QueryValueEx(sub, "DisplayName")[0]).strip()
                                if title.casefold() not in (name, name + " app"):
                                    continue
                                try:
                                    version = str(winreg.QueryValueEx(sub, "DisplayVersion")[0]).strip()
                                except OSError:
                                    version = None
                                try:
                                    folder = str(winreg.QueryValueEx(sub, "InstallLocation")[0]).strip()
                                except OSError:
                                    folder = ""
                                return {"version": version, "location": folder or None,
                                        "linux_format": None, "standalone": False,
                                        "source": "Windows installation record"}
                        except (OSError, ValueError):
                            continue
            except OSError:
                continue
    return None


def _windows_exe_version(executable):
    """Read executable version metadata through the Windows version API."""
    from windows_native import executable_version
    return executable_version(executable)


def _windows_portable(app):
    name = app_name(app)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    folders = [base / "Programs" / "CraftApps" / name,
               base / "Programs" / name,
               Path(os.environ.get("ProgramFiles", "C:/Program Files")) / name]
    for folder in folders:
        if not folder.is_dir():
            continue
        executables = list(folder.glob(app + ".exe")) + list(folder.glob("*/" + app + ".exe"))
        if executables:
            return {"version": _windows_exe_version(executables[0]),
                    "location": str(executables[0]), "linux_format": None,
                    "standalone": "CraftApps" in folder.parts,
                    "source": "Windows application executable"}
    return None


def _mac_installed(app):
    label = app_name(app)
    candidates = [Path.home() / "Applications" / (label + ".app"),
                  Path("/Applications") / (label + ".app")]
    for root in (Path.home() / "Applications", Path("/Applications")):
        if root.is_dir():
            candidates.extend(p for p in root.glob("*.app") if p.stem.casefold() == label.casefold())
    for bundle in candidates:
        if not bundle.is_dir():
            continue
        try:
            with (bundle / "Contents" / "Info.plist").open("rb") as f:
                details = plistlib.load(f)
            version = details.get("CFBundleShortVersionString") or details.get("CFBundleVersion")
        except (OSError, ValueError, TypeError):
            version = None
        return {"version": str(version) if version else None, "location": str(bundle),
                "linux_format": None, "standalone": False, "source": "macOS application bundle"}
    return None


def _linux_installed(app):
    if shutil.which("dpkg-query"):
        version = _command(["dpkg-query", "-W", "-f=${Version}", app])
        if version:
            return {"version": version, "location": None, "linux_format": "DEB",
                    "standalone": False, "source": "dpkg"}
    if shutil.which("rpm"):
        version = _command(["rpm", "-q", "--qf", "%{VERSION}", app])
        if version:
            return {"version": version, "location": None, "linux_format": "RPM",
                    "standalone": False, "source": "rpm"}
    if shutil.which("flatpak"):
        app_id = "ai.storyteller." + app
        version = _command(["flatpak", "info", "--show-version", app_id])
        if version:
            return {"version": version, "location": None, "linux_format": "Flatpak",
                    "standalone": False, "source": "Flatpak"}
    binary = Path.home() / ".local/bin" / (app + ".AppImage")
    if binary.is_file():
        return {"version": None, "location": str(binary), "linux_format": "AppImage",
                "standalone": True, "source": "AppImage"}
    folder = Path.home() / ".local/opt/CraftApps" / app_name(app)
    if folder.is_dir() and (list(folder.glob(app)) or list(folder.glob("*/" + app))):
        return {"version": None, "location": str(folder), "linux_format": "Tarball",
                "standalone": True, "source": "portable tarball"}
    return None


def detect_installed(app, system):
    """Combine native detection with a recorded receipt, without trusting stale paths."""
    app_name(app)
    if system == "Windows":
        found = _windows_registry(app) or _windows_portable(app)
    elif system == "macOS":
        found = _mac_installed(app)
    elif system == "Linux":
        found = _linux_installed(app)
    else:
        return None

    receipt = read_receipts(system).get(app, {})
    if not isinstance(receipt, dict) or receipt.get("system") != system:
        receipt = {}
    saved_location = receipt.get("location")
    saved_present = isinstance(saved_location, str) and Path(saved_location).exists()
    if not found and not saved_present:
        return None
    found = dict(found or {})
    if saved_present and not found.get("location"):
        found["location"] = saved_location
    # A native package manager's version reflects out-of-band upgrades.
    if not found.get("version") and (found or saved_present):
        found["version"] = receipt.get("version")
    if receipt and (saved_present or found):
        for key in ("desktop", "shortcut_label", "arch"):
            found[key] = receipt.get(key)
        if saved_present and receipt.get("standalone"):
            found["standalone"] = True
        if saved_present and receipt.get("linux_format"):
            found["linux_format"] = receipt["linux_format"]
    return found
