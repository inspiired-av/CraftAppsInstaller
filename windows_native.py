"""Native Windows shortcuts, installed-executable lookup and file versions.

Uses ctypes, winreg and the Windows Shell interfaces directly, without
PowerShell, encoded scripts, or third-party runtime dependencies.
"""
import ctypes
import os
import sys
import uuid
from pathlib import Path

# Never load Windows DLLs on other platforms.
def _require_windows():
    if sys.platform != "win32":
        raise OSError("Windows APIs are available only on Windows")


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


_CLSID_SHELL_LINK = _GUID.parse("00021401-0000-0000-C000-000000000046")
_IID_SHELL_LINK_W = _GUID.parse("000214F9-0000-0000-C000-000000000046")
_IID_PERSIST_FILE = _GUID.parse("0000010b-0000-0000-C000-000000000046")
_FID_DESKTOP = _GUID.parse("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
_FID_PROGRAMS = _GUID.parse("A77F5D77-2E2B-44C3-A6A2-ABA601054A51")
_FID_COMMON_PROGRAMS = _GUID.parse("0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8")


def _check_hr(hr, operation):
    if hr < 0:
        raise OSError(f"{operation} failed with HRESULT 0x{hr & 0xffffffff:08X}")


def _com_method(obj, index, return_type, *parameters):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(return_type, ctypes.c_void_p, *parameters)
    return fn(table[index])


class _ShellLink:
    def __enter__(self):
        _require_windows()
        self._ole32 = ctypes.WinDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        hr = self._ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        # If already initialized with another threading model, COM can still
        # be used by the current thread. Do not uninitialize it in that case.
        if hr < 0 and (hr & 0xffffffff) != 0x80010106:
            _check_hr(hr, "CoInitializeEx")
        self._uninit = hr in (0, 1)
        self._obj = ctypes.c_void_p()
        self._persist = ctypes.c_void_p()
        self._ole32.CoCreateInstance.argtypes = (
            ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        self._ole32.CoCreateInstance.restype = ctypes.c_long
        try:
            _check_hr(self._ole32.CoCreateInstance(
                ctypes.byref(_CLSID_SHELL_LINK), None, 1,
                ctypes.byref(_IID_SHELL_LINK_W), ctypes.byref(self._obj)),
                "CoCreateInstance(IShellLinkW)")
            _check_hr(_com_method(
                self._obj, 0, ctypes.c_long, ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p))(
                    self._obj, ctypes.byref(_IID_PERSIST_FILE),
                    ctypes.byref(self._persist)),
                "QueryInterface(IPersistFile)")
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        for obj in (self._persist, self._obj):
            if obj.value:
                _com_method(obj, 2, ctypes.c_ulong)(obj)  # IUnknown.Release
        if self._uninit:
            self._ole32.CoUninitialize()

    def create(self, path, target):
        _check_hr(_com_method(self._obj, 20, ctypes.c_long, ctypes.c_wchar_p)(
            self._obj, str(target)), "IShellLinkW.SetPath")
        _check_hr(_com_method(self._obj, 9, ctypes.c_long, ctypes.c_wchar_p)(
            self._obj, str(target.parent)), "IShellLinkW.SetWorkingDirectory")
        _check_hr(_com_method(self._obj, 17, ctypes.c_long,
                              ctypes.c_wchar_p, ctypes.c_int)(
                                  self._obj, str(target), 0),
                  "IShellLinkW.SetIconLocation")
        _check_hr(_com_method(self._persist, 6, ctypes.c_long,
                              ctypes.c_wchar_p, ctypes.c_int)(
                                  self._persist, str(path), True),
                  "IPersistFile.Save")

    def read_target(self, path):
        _check_hr(_com_method(self._persist, 5, ctypes.c_long,
                              ctypes.c_wchar_p, ctypes.c_uint32)(
                                  self._persist, str(path), 0),
                  "IPersistFile.Load")
        buffer = ctypes.create_unicode_buffer(32768)
        _check_hr(_com_method(self._obj, 3, ctypes.c_long,
                              ctypes.POINTER(ctypes.c_wchar), ctypes.c_int,
                              ctypes.c_void_p, ctypes.c_uint32)(
                                  self._obj, buffer, len(buffer), None, 0),
                  "IShellLinkW.GetPath")
        return Path(buffer.value) if buffer.value else None


def known_folder(which):
    _require_windows()
    folder_ids = {"Desktop": _FID_DESKTOP, "Programs": _FID_PROGRAMS,
                  "CommonPrograms": _FID_COMMON_PROGRAMS}
    if which not in folder_ids:
        raise ValueError("Unknown Windows known-folder name")
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = (
        ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p))
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    out = ctypes.c_void_p()
    _check_hr(shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_ids[which]), 0, None, ctypes.byref(out)),
        "SHGetKnownFolderPath")
    try:
        return Path(ctypes.wstring_at(out))
    finally:
        ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        ole32.CoTaskMemFree(out)


def shortcut_target(path):
    with _ShellLink() as link:
        return link.read_target(Path(path))


def create_shortcut(target, label, *, desktop=False):
    """Write a Windows .lnk to an installed EXE, then verify its target."""
    _require_windows()
    target = Path(target)
    if not target.is_file() or target.suffix.lower() != ".exe":
        raise ValueError("Shortcut target must be an existing .exe file")
    if (not label or label in (".", "..") or label.endswith((" ", "."))
            or any(c in label for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in label)):
        raise ValueError("Invalid Windows shortcut name")
    folder = known_folder("Desktop" if desktop else "Programs")
    folder.mkdir(parents=True, exist_ok=True)
    shortcut = folder / (label + ".lnk")
    with _ShellLink() as link:
        link.create(shortcut, target)
    saved = shortcut_target(shortcut)
    if saved is None or str(saved.resolve()).casefold() != str(target.resolve()).casefold():
        shortcut.unlink(missing_ok=True)
        raise OSError("Shortcut target verification failed")
    return shortcut


def _reg_string(key, field):
    import winreg
    try:
        value = winreg.QueryValueEx(key, field)[0]
    except OSError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _registered_executables(app, display):
    import winreg
    exe_name = app + ".exe"
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    app_paths = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + exe_name
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                if (_reg_string(entry, "DisplayName") or "").casefold() != display.casefold():
                                    continue
                                folder = _reg_string(entry, "InstallLocation")
                                if folder:
                                    yield Path(os.path.expandvars(folder)) / exe_name
                        except OSError:
                            continue
            except OSError:
                pass
            try:
                with winreg.OpenKey(hive, app_paths, 0, winreg.KEY_READ | view) as key:
                    location = _reg_string(key, None)
                    if location:
                        yield Path(os.path.expandvars(location.strip('"')))
            except OSError:
                pass


def find_installed_executable(app, display):
    """Resolve executable from registry, Start Menu links, or standard folders."""
    _require_windows()
    exe_name = app + ".exe"
    candidates = list(_registered_executables(app, display))
    for folder_type in ("Programs", "CommonPrograms"):
        try:
            folder = known_folder(folder_type)
            if folder.is_dir():
                for link in folder.rglob("*.lnk"):
                    if link.stem.casefold() not in (display.casefold(), app.casefold()):
                        continue
                    try:
                        target = shortcut_target(link)
                        if target:
                            candidates.append(target)
                    except (OSError, ValueError):
                        pass
        except OSError:
            pass
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        str(Path(os.environ.get("LOCALAPPDATA",
                                str(Path.home() / "AppData/Local"))) / "Programs"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if root:
            for dirname in (display, display + " App"):
                candidates.append(Path(root) / dirname / exe_name)
    for candidate in candidates:
        if candidate.name.casefold() == exe_name.casefold() and candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f"Cannot locate installed {exe_name}; desktop shortcut not created")


class _VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
        "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask",
        "dwFileFlags", "dwFileOS", "dwFileType", "dwFileSubtype",
        "dwFileDateMS", "dwFileDateLS")]


def executable_version(path):
    """Read signed/unsigned PE fixed product version metadata without executing."""
    _require_windows()
    path = Path(path)
    if not path.is_file():
        return None
    dll = ctypes.WinDLL("version", use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
    dll.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    dll.GetFileVersionInfoW.argtypes = (
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    dll.GetFileVersionInfoW.restype = ctypes.c_int
    dll.VerQueryValueW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p,
                                  ctypes.POINTER(ctypes.c_void_p),
                                  ctypes.POINTER(ctypes.c_uint32))
    dll.VerQueryValueW.restype = ctypes.c_int
    length = dll.GetFileVersionInfoSizeW(str(path), None)
    if not length:
        return None
    data = ctypes.create_string_buffer(length)
    if not dll.GetFileVersionInfoW(str(path), 0, length, data):
        return None
    pointer = ctypes.c_void_p()
    size = ctypes.c_uint32()
    if not dll.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(size)):
        return None
    if size.value < ctypes.sizeof(_VS_FIXEDFILEINFO):
        return None
    info = ctypes.cast(pointer, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
    if info.dwSignature != 0xFEEF04BD:
        return None
    ms, ls = info.dwProductVersionMS, info.dwProductVersionLS
    if not (ms or ls):
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    if not (ms or ls):
        return None
    a, b, c, d = ms >> 16, ms & 0xffff, ls >> 16, ls & 0xffff
    return f"{a}.{b}.{c}.{d}"
