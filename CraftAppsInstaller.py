#!/usr/bin/env python3
"""Craft Apps installer. Python 3.9+, standard library only."""
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

OWNER = 'storytold'
APPS = ['photocraft', 'lightcraft', 'filmcraft', 'effectcraft', 'designcraft',
        'pdfcraft', 'vectorcraft', 'wordcraft', 'gridcraft', 'deckcraft']
APP_LABELS = {
    'photocraft': 'PhotoCraft (Photoshop)',
    'lightcraft': 'LightCraft (Lightroom)',
    'filmcraft': 'FilmCraft (Premiere Pro)',
    'effectcraft': 'EffectCraft (After Effects)',
    'designcraft': 'DesignCraft (InDesign)',
    'pdfcraft': 'PdfCraft (Acrobat)',
    'vectorcraft': 'VectorCraft (Illustrator)',
    'wordcraft': 'WordCraft (Microsoft Word)',
    'gridcraft': 'GridCraft (Microsoft Excel)',
    'deckcraft': 'DeckCraft (Microsoft PowerPoint)',
}

def display_name(app):
    return app[:1].upper() + app[1:-5] + 'Craft'

def shortcut_name(app, include_similar=False):
    """Choose the desktop shortcut label without changing the installed app."""
    return APP_LABELS[app] if include_similar else display_name(app)
API = 'https://api.github.com'
USER_AGENT = 'CraftAppsInstaller/1.0'

def fetch_json(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
        'User-Agent': USER_AGENT, 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def detect_os():
    return {'Windows': 'Windows', 'Darwin': 'macOS', 'Linux': 'Linux'}.get(platform.system(), 'Unknown')

def detect_arch():
    machine = platform.machine().lower()
    return 'arm64' if machine in ('aarch64', 'arm64') else 'x64' if machine in ('amd64', 'x86_64') else 'unknown'

def local_linux_format():
    if shutil.which('apt-get') or shutil.which('dpkg'): return 'DEB'
    if shutil.which('dnf') or shutil.which('rpm'): return 'RPM'
    return 'AppImage'

def get_releases(app):
    releases = fetch_json(f'{API}/repos/{OWNER}/{app}/releases?per_page=100')
    return [r for r in releases if not r.get('draft')]

def latest_release(releases):
    return next((r for r in releases if not r.get('prerelease')), releases[0] if releases else None)

def choose_asset(release, system, arch, linux_fmt, standalone=False):
    assets = release.get('assets', [])
    valid = []
    for asset in assets:
        name = asset['name'].lower()
        if (name.endswith(('.sha256', '.sha256sum', '.txt', '.zsync', '.sig'))
                or 'source' in name or '-web-' in name or '-cli-' in name):
            continue
        if system == 'Windows':
            matches_arch = ((arch == 'x64' and ('x64' in name or 'x86_64' in name)) or
                            (arch == 'arm64' and ('arm64' in name or 'aarch64' in name)))
            if 'windows' in name and matches_arch and name.endswith(('.zip', '.msi', '.exe')):
                portable = 'portable' in name and name.endswith('.zip')
                if standalone and not portable:
                    continue
                if not standalone and portable:
                    continue
                priority = 0 if name.endswith('.msi') else 1 if name.endswith('.exe') else 2
                valid.append((priority, asset))
        elif system == 'macOS':
            # The desktop application is a self-contained .app distributed in
            # a universal DMG (no separate portable macOS release).
            if ('macos' in name or 'darwin' in name) and 'universal' in name:
                if name.endswith('.dmg') or (not standalone and name.endswith('.pkg')):
                    priority = 0 if name.endswith('.dmg') else 1
                    valid.append((priority, asset))
        elif system == 'Linux':
            if 'linux' not in name: continue
            matches_arch = (arch == 'arm64' and ('aarch64' in name or 'arm64' in name)) or (arch == 'x64' and ('x86_64' in name or 'amd64' in name))
            if not matches_arch: continue
            if standalone:
                suffix = '.tar.gz' if linux_fmt == 'Tarball' else '.appimage'
            else:
                suffix = {'DEB': '.deb', 'RPM': '.rpm', 'AppImage': '.appimage',
                          'Flatpak': '.flatpak', 'Tarball': '.tar.gz'}[linux_fmt]
            if name.endswith(suffix): valid.append((0, asset))
    return min(valid, key=lambda x: (x[0], x[1]['name']))[1] if valid else None

def download(asset, directory, log):
    filename = asset['name']
    if not filename or Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise RuntimeError('Invalid release asset filename')
    path = directory / filename
    partial = directory / (filename + '.part')
    url = asset['browser_download_url']
    if not url.startswith(f'https://github.com/{OWNER}/'):
        raise RuntimeError('Refusing unexpected download URL')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=120) as source, open(partial, 'wb') as target:
            while True:
                block = source.read(1024 * 1024)
                if not block: break
                digest.update(block)
                target.write(block)
        expected = asset.get('digest')
        if expected and expected.startswith('sha256:'):
            if digest.hexdigest().lower() != expected.split(':', 1)[1].lower():
                raise RuntimeError('SHA-256 checksum mismatch')
            log('SHA-256 verified')
        else:
            log('No API checksum available; download not hash-verified')
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path

def safe_unzip(src, dst):
    with zipfile.ZipFile(src) as z:
        base = dst.resolve()
        for member in z.infolist():
            output = (dst / member.filename).resolve()
            if not output.is_relative_to(base):
                raise RuntimeError('Unsafe ZIP entry')
            mode = (member.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RuntimeError('ZIP contains symlink; refusing extraction')
        z.extractall(dst)

def safe_untar(src, dst):
    with tarfile.open(src, 'r:gz') as tf:
        base = dst.resolve()
        for member in tf.getmembers():
            output = (dst / member.name).resolve()
            if not output.is_relative_to(base):
                raise RuntimeError('Unsafe tar archive entry')
            if not (member.isfile() or member.isdir()):
                raise RuntimeError('Tar archive contains links or special files')
        tf.extractall(dst)

def run_windows_powershell(script):
    """Run PowerShell without shell quoting ambiguities; return stdout."""
    import base64
    command = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', command],
        check=True, capture_output=True, text=True)
    return result.stdout.strip()


def windows_find_installed_executable(app):
    """Resolve a *real* installed app EXE after MSI/EXE setup, not the setup file.

    The installed location can differ between machine-wide and per-user setups.
    Prefer registry installation records, then actual Start Menu link targets, then
    standard app installation folders. Do not guess if none contains the EXE.
    """
    import base64
    display = display_name(app)
    quote = lambda val: "'" + str(val).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference = 'Stop'
$appName = {quote(display)}
$exeName = {quote(app + '.exe')}
$candidates = [System.Collections.Generic.List[string]]::new()

# Uninstall records often contain the MSI-selected installation directory.
$uninstall = @(
    'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
    'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
)
foreach ($registryPath in $uninstall) {{
    foreach ($entry in @(Get-ItemProperty -Path $registryPath -ErrorAction SilentlyContinue)) {{
        if ($null -eq $entry -or $entry.DisplayName -ine $appName) {{ continue }}
        if ($entry.InstallLocation) {{
            $candidates.Add((Join-Path $entry.InstallLocation $exeName))
        }}
    }}
}}

# App Paths is another authoritative Windows registration for an EXE.
foreach ($hive in @('HKLM:', 'HKCU:')) {{
    $key = Join-Path $hive ('SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + $exeName)
    $entry = Get-Item -LiteralPath $key -ErrorAction SilentlyContinue
    if ($entry) {{
        $value = $entry.GetValue('')
        if ($value) {{ $candidates.Add([string]$value) }}
    }}
}}

# Installer-created Start Menu shortcuts can reveal custom install locations.
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Programs'),
                      [Environment]::GetFolderPath('CommonPrograms'))) {{
    if (-not $folder -or -not (Test-Path -LiteralPath $folder)) {{ continue }}
    foreach ($link in @(Get-ChildItem -LiteralPath $folder -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue)) {{
        if ($link.BaseName -ine $appName -and $link.BaseName -ine $exeName.Replace('.exe','')) {{ continue }}
        try {{
            $target = $shell.CreateShortcut($link.FullName).TargetPath
            if ($target) {{ $candidates.Add($target) }}
        }} catch {{}}
    }}
}}

# Normal installation defaults: e.g. C:\\Program Files\\GridCraft\\gridcraft.exe.
$roots = @($env:ProgramFiles, ${{env:ProgramFiles(x86)}},
           (Join-Path $env:LOCALAPPDATA 'Programs'), $env:LOCALAPPDATA)
foreach ($root in $roots) {{
    if (-not $root) {{ continue }}
    $folder = Join-Path $root $appName
    $candidates.Add((Join-Path $folder $exeName))
    $candidates.Add((Join-Path (Join-Path $root ($appName + ' App')) $exeName))
}}

foreach ($candidate in $candidates) {{
    if (-not $candidate) {{ continue }}
    if ([System.IO.Path]::GetFileName($candidate) -ine $exeName) {{ continue }}
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {{
        # Encode the path for reliable round-tripping with non-ASCII user folders.
        $resolved = (Get-Item -LiteralPath $candidate).FullName
        [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($resolved)))
        exit 0
    }}
}}
throw "Cannot locate installed $exeName. Desktop shortcut will not be created."
"""
    found = run_windows_powershell(script).splitlines()
    if not found or not found[-1]:
        raise RuntimeError(f'Could not resolve installed executable for {display}')
    try:
        return Path(base64.b64decode(found[-1], validate=True).decode('utf-16le'))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('Installed executable lookup returned an invalid path') from exc


def windows_shortcut(display, executable, desktop=False, shortcut_label=None):
    """Create a real .lnk whose target is the installed program's executable."""
    # PowerShell COM is installed with Windows; no third-party libraries necessary.
    quote = lambda val: "'" + str(val).replace("'", "''") + "'"
    if desktop:
        root = "[Environment]::GetFolderPath('DesktopDirectory')"
    else:
        root = "[Environment]::GetFolderPath('Programs')"
    script = (f"$folder={root}; "
              "if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }; "
              f"$destination=Join-Path $folder {quote((shortcut_label or display) + '.lnk')}; "
              f"$exe={quote(executable)}; "
              "if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw ('Missing installed executable: ' + $exe) }; "
              "if ([IO.Path]::GetExtension($exe) -ine '.exe') { throw 'Shortcut must target an .exe' }; "
              "$w=New-Object -ComObject WScript.Shell; "
              "$s=$w.CreateShortcut($destination); "
              "$s.TargetPath=$exe; "
              "$s.WorkingDirectory=Split-Path -Parent $exe; "
              "$s.IconLocation=($exe + ',0'); $s.Save(); "
              "$saved=$w.CreateShortcut($destination); "
              "if ($saved.TargetPath -ine $exe) { Remove-Item -LiteralPath $destination -Force; throw 'Shortcut target verification failed' }")
    run_windows_powershell(script)

def install_windows(app, path, log, desktop=False, shortcut_label=None):
    display = display_name(app)
    name = path.name.lower()
    if name.endswith('.zip'):
        root = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Programs' / 'CraftApps'
        root.mkdir(parents=True, exist_ok=True)
        dest = root / display
        with tempfile.TemporaryDirectory(dir=root) as td:
            stage = Path(td) / 'payload'
            stage.mkdir()
            safe_unzip(path, stage)
            executables = list(stage.rglob('*.exe'))
            preferred = [exe for exe in executables if exe.stem.lower() == app.lower()]
            gui = preferred or [exe for exe in executables if not any(t in exe.stem.lower() for t in ('cli', 'uninstall', 'setup'))]
            if not gui: raise RuntimeError('No application executable found inside ZIP')
            exe_relative = (gui[0]).relative_to(stage)
            if dest.exists():
                backup = root / (display + '.previous')
                if backup.exists(): shutil.rmtree(backup)
                dest.rename(backup)
                try:
                    shutil.move(str(stage), str(dest))
                except Exception:
                    backup.rename(dest)
                    raise
                shutil.rmtree(backup)
            else:
                shutil.move(str(stage), str(dest))
        executable = dest / exe_relative
        try:
            windows_shortcut(display, executable)
            log(f'Installed to {dest}; Start Menu shortcut -> {executable}')
        except Exception as e:
            log(f'Installed to {dest}; shortcut creation failed: {e}')
        if desktop:
            try:
                windows_shortcut(display, executable, desktop=True, shortcut_label=shortcut_label)
                log(f'Desktop shortcut created: {shortcut_label or display} -> {executable}')
            except Exception as e:
                log(f'Desktop shortcut could not be created: {e}')
    elif name.endswith('.msi'):
        subprocess.run(['msiexec.exe', '/i', str(path), '/passive', '/norestart'], check=True)
    elif name.endswith('.exe'):
        # Do not guess unattended flags for arbitrary EXE installers.
        subprocess.run([str(path)], check=True)
    else: raise RuntimeError('Unsupported Windows package')
    if desktop and name.endswith(('.msi', '.exe')):
        try:
            executable = windows_find_installed_executable(app)
            windows_shortcut(display, executable, desktop=True, shortcut_label=shortcut_label)
            log(f'Desktop shortcut created: {shortcut_label or display} -> {executable}')
        except Exception as e:
            log(f'Installed, but desktop shortcut could not be created: {e}')

def macos_desktop_shortcut(bundle, log, shortcut_label=None):
    desktop = Path.home() / 'Desktop'
    desktop.mkdir(exist_ok=True)
    shortcut = desktop / (f'{shortcut_label}.app' if shortcut_label else bundle.name)
    if shortcut.is_symlink() and shortcut.resolve() == bundle.resolve():
        return
    if shortcut.exists() or shortcut.is_symlink():
        raise RuntimeError(f'Desktop entry already exists: {shortcut}')
    shortcut.symlink_to(bundle, target_is_directory=True)
    log(f'Desktop application link created: {shortcut}')

def install_macos(app, path, log, desktop=False, shortcut_label=None):
    suffix = path.suffix.lower()
    if suffix == '.pkg':
        subprocess.run(['sudo', 'installer', '-pkg', str(path), '-target', '/'], check=True)
        if desktop:
            log('A PKG installer controls the application location; desktop link not created')
    elif suffix == '.dmg':
        with tempfile.TemporaryDirectory() as mount:
            subprocess.run(['hdiutil', 'attach', str(path), '-mountpoint', mount, '-nobrowse', '-quiet'], check=True)
            try:
                bundles = list(Path(mount).rglob('*.app'))
                if not bundles: raise RuntimeError('No .app bundle inside DMG')
                target_dir = Path.home() / 'Applications'
                target_dir.mkdir(exist_ok=True)
                for bundle in bundles:
                    target = target_dir / bundle.name
                    if target.exists(): shutil.rmtree(target)
                    shutil.copytree(bundle, target, symlinks=True)
                    log(f'Installed {target}')
                    if desktop:
                        try: macos_desktop_shortcut(target, log, shortcut_label)
                        except Exception as exc: log(f'Desktop link not created: {exc}')
            finally:
                subprocess.run(['hdiutil', 'detach', mount, '-quiet'], check=False)
    else: raise RuntimeError('Unsupported macOS package')

def linux_desktop_launcher(app, executable, log):
    """Create a freedesktop launcher for installed standalone applications."""
    label = display_name(app)
    applications = Path.home() / '.local/share/applications'
    applications.mkdir(parents=True, exist_ok=True)
    launcher = applications / f'{app}-craftapps.desktop'
    # A freedesktop Exec command uses double quotes and escaped reserved chars.
    quoted_exec = '"' + str(executable).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    content = (f'[Desktop Entry]\nType=Application\nName={label}\n'
               f'Exec={quoted_exec}\nTerminal=false\nCategories=Graphics;Office;\n')
    launcher.write_text(content, encoding='utf-8')
    launcher.chmod(0o755)
    log(f'Application launcher registered: {launcher}')
    return launcher

def linux_desktop_shortcut(app, log, launcher=None, shortcut_label=None):
    if launcher is None:
        folders = [Path.home() / '.local/share/applications',
                   Path.home() / '.local/share/flatpak/exports/share/applications',
                   Path('/usr/local/share/applications'), Path('/usr/share/applications'),
                   Path('/var/lib/flatpak/exports/share/applications')]
        candidates = []
        for directory in folders:
            if directory.is_dir():
                candidates.extend(p for p in directory.glob('*.desktop')
                                  if app.lower() in p.stem.lower())
        if not candidates:
            raise RuntimeError(f'No installed .desktop launcher found for {display_name(app)}')
        launcher = sorted(candidates, key=lambda p: len(p.stem))[0]
    desktop = Path.home() / 'Desktop'
    desktop.mkdir(exist_ok=True)
    label = shortcut_label or display_name(app)
    target = desktop / f'{label}.desktop'
    shutil.copy2(launcher, target)
    # The visible Linux desktop icon name comes from Name=, not the filename.
    # Customize only the Desktop copy; retain the original application launcher.
    import re
    contents = target.read_text(encoding='utf-8')
    if re.search(r'(?m)^Name=', contents):
        contents = re.sub(r'(?m)^Name=[^\r\n]*', lambda _: 'Name=' + label, contents, count=1)
    else:
        contents = contents.replace('[Desktop Entry]\n', '[Desktop Entry]\nName=' + label + '\n', 1)
    target.write_text(contents, encoding='utf-8')
    target.chmod(target.stat().st_mode | 0o111)
    log(f'Desktop launcher created: {target} (some desktops require Allow Launching)')

def install_linux(app, path, log, desktop=False, shortcut_label=None):
    lower = path.name.lower()
    standalone_exe = None
    if lower.endswith('.deb'):
        subprocess.run(['sudo', 'apt-get', 'install', '-y', str(path.resolve())], check=True)
    elif lower.endswith('.rpm'):
        manager = shutil.which('dnf') or shutil.which('zypper')
        if not manager: raise RuntimeError('dnf or zypper required to install RPM')
        args = ['sudo', manager, 'install', '-y', str(path)] if Path(manager).name == 'dnf' else ['sudo', manager, '--non-interactive', 'install', str(path)]
        subprocess.run(args, check=True)
    elif lower.endswith('.appimage'):
        dest = Path.home() / '.local' / 'bin'
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f'{app}.AppImage'
        shutil.copy2(path, target)
        target.chmod(target.stat().st_mode | 0o111)
        log(f'Installed AppImage at {target}')
        standalone_exe = target
    elif lower.endswith('.tar.gz'):
        root = Path.home() / '.local' / 'opt' / 'CraftApps'
        root.mkdir(parents=True, exist_ok=True)
        dest = root / display_name(app)
        with tempfile.TemporaryDirectory(dir=root) as td:
            stage = Path(td) / 'payload'
            stage.mkdir()
            safe_untar(path, stage)
            binaries = [p for p in stage.rglob('*') if p.is_file() and os.access(p, os.X_OK)
                        and p.name.lower() == app]
            if not binaries: raise RuntimeError('No executable found in Linux tarball')
            rel = binaries[0].relative_to(stage)
            if dest.exists(): shutil.rmtree(dest)
            shutil.move(str(stage), str(dest))
        standalone_exe = dest / rel
        log(f'Installed tarball at {dest}')
    elif lower.endswith('.flatpak'):
        subprocess.run(['flatpak', 'install', '-y', '--user', str(path)], check=True)
    else: raise RuntimeError('Unsupported Linux package')
    launcher = linux_desktop_launcher(app, standalone_exe, log) if standalone_exe else None
    if desktop:
        try: linux_desktop_shortcut(app, log, launcher, shortcut_label)
        except Exception as exc: log(f'Installed, but desktop shortcut not created: {exc}')

class InstallerUI:
    def __init__(self, root):
        self.root = root
        root.title('Craft Apps Installer')
        root.geometry('930x860')
        self.messages = queue.Queue()
        self.releases = {}
        self.rows = {}
        self.busy = False
        area = ttk.Frame(root, padding=14)
        area.pack(fill='both', expand=True)
        ttk.Label(area, text='Craft Apps Installer', font=('TkDefaultFont', 17, 'bold')).pack(anchor='w')
        ttk.Label(area, text='Choose apps and versions from storytold GitHub releases. Latest stable is the default.').pack(anchor='w', pady=(3, 12))
        options = ttk.Frame(area)
        options.pack(fill='x', pady=(0, 10))
        detected = detect_os()
        self.system = tk.StringVar(value=f'Auto ({detected})')
        ttk.Label(options, text='Operating system:').grid(row=0, column=0, sticky='w')
        self.osbox = ttk.Combobox(options, textvariable=self.system, state='readonly', width=20,
                                  values=[f'Auto ({detected})', 'Windows', 'macOS', 'Linux'])
        self.osbox.grid(row=0, column=1, padx=(9, 24))
        self.arch = tk.StringVar(value=f'Auto ({detect_arch()})')
        self.last_non_mac_arch = self.arch.get()
        self.arch_label = ttk.Label(options, text='Architecture:')
        self.arch_label.grid(row=0, column=2)
        self.archbox = ttk.Combobox(options, textvariable=self.arch, state='readonly', width=17,
                                   values=[f'Auto ({detect_arch()})', 'x64', 'arm64'])
        self.archbox.grid(row=0, column=3, padx=(9, 0))
        fmt = ttk.Frame(area)
        fmt.pack(fill='x', pady=(0, 10))
        self.linuxfmt = tk.StringVar(value=local_linux_format())
        self.last_installer_linuxfmt = self.linuxfmt.get()
        self.was_standalone = False
        self.linuxfmt_label = ttk.Label(fmt, text='Linux package format:')
        self.linuxfmt_label.pack(side='left')
        self.linuxfmtbox = ttk.Combobox(fmt, state='readonly', width=14, textvariable=self.linuxfmt,
                                       values=['DEB', 'RPM', 'AppImage', 'Flatpak', 'Tarball'])
        self.linuxfmtbox.pack(side='left', padx=10)
        self.standalone = tk.BooleanVar(value=False)
        self.standalone_box = ttk.Checkbutton(fmt, text='Standalone / portable version',
                                              variable=self.standalone)
        self.standalone_box.pack(side='left', padx=(14, 6))
        self.download_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(fmt, text='Download only (do not install)', variable=self.download_only).pack(side='left', padx=(12, 0))
        shortcut_options = ttk.Frame(area)
        shortcut_options.pack(fill='x', pady=(0, 10))
        self.desktop_icons = tk.BooleanVar(value=False)
        self.desktop_box = ttk.Checkbutton(shortcut_options, text='Create desktop shortcuts after installation',
                                           variable=self.desktop_icons)
        self.desktop_box.pack(side='left')
        shortcut_naming_row = ttk.Frame(area)
        shortcut_naming_row.pack(fill='x', pady=(0, 10))
        self.shortcut_naming = tk.StringVar(value='program')
        self.shortcut_naming_label = ttk.Label(shortcut_naming_row, text='Shortcut names:')
        self.shortcut_naming_label.pack(side='left')
        self.shortcut_name_program = ttk.Radiobutton(
            shortcut_naming_row, text='Program name (e.g. PdfCraft)',
            variable=self.shortcut_naming, value='program')
        self.shortcut_name_program.pack(side='left', padx=(10, 16))
        self.shortcut_name_similar = ttk.Radiobutton(
            shortcut_naming_row, text='Include similar application (e.g. PdfCraft (Acrobat))',
            variable=self.shortcut_naming, value='similar')
        self.shortcut_name_similar.pack(side='left')
        self.package_note = tk.StringVar(value='')
        ttk.Label(shortcut_options, textvariable=self.package_note, foreground='#666666').pack(side='right')
        destination = ttk.Frame(area)
        destination.pack(fill='x', pady=(0, 10))
        ttk.Label(destination, text='Download folder:').pack(side='left')
        self.download_dir = tk.StringVar(value=str(Path.home() / 'Downloads' / 'CraftApps'))
        self.dir_entry = ttk.Entry(destination, textvariable=self.download_dir)
        self.dir_entry.pack(side='left', fill='x', expand=True, padx=(10, 8))
        self.browse_button = ttk.Button(destination, text='Browse...', command=self.browse_folder)
        self.browse_button.pack(side='right')
        ttk.Separator(area).pack(fill='x', pady=6)
        table = ttk.Frame(area)
        table.pack(fill='x')
        ttk.Label(table, text='App (similar commercial application)', width=41).grid(row=0, column=0, sticky='w')
        ttk.Label(table, text='Version', width=25).grid(row=0, column=1, sticky='w')
        for idx, app in enumerate(APPS, 1):
            enabled = tk.BooleanVar(value=False)
            version = tk.StringVar(value='Latest stable')
            ttk.Checkbutton(table, text=APP_LABELS[app], variable=enabled).grid(row=idx, column=0, sticky='w', pady=4)
            combo = ttk.Combobox(table, textvariable=version, state='readonly', width=27, values=['Latest stable'])
            combo.grid(row=idx, column=1, sticky='w', padx=(5, 8), pady=4)
            self.rows[app] = (enabled, version, combo)
        controls = ttk.Frame(area)
        controls.pack(fill='x', pady=(13, 6))
        self.refresh = ttk.Button(controls, text='Load available versions', command=self.load_versions)
        self.refresh.pack(side='left')
        ttk.Button(controls, text='Select all', command=lambda: self.select_all(True)).pack(side='left', padx=7)
        ttk.Button(controls, text='Clear', command=lambda: self.select_all(False)).pack(side='left')
        self.go = ttk.Button(controls, text='Download and install selected', command=self.start)
        self.go.pack(side='right')
        self.system.trace_add('write', lambda *_: self.update_button())
        self.arch.trace_add('write', lambda *_: self.update_button())
        self.download_only.trace_add('write', lambda *_: self.update_button())
        self.standalone.trace_add('write', lambda *_: self.update_button())
        self.desktop_icons.trace_add('write', lambda *_: self.update_button())
        self.update_button()
        ttk.Label(area, text='Activity log').pack(anchor='w', pady=(8, 4))
        logframe = ttk.Frame(area)
        logframe.pack(fill='both', expand=True)
        self.logbox = tk.Text(logframe, height=12, wrap='word', state='disabled')
        self.logbox.pack(side='left', fill='both', expand=True)
        ttk.Scrollbar(logframe, orient='vertical', command=self.logbox.yview).pack(side='right', fill='y')
        self.logbox.configure(yscrollcommand=lambda *x: None)
        ttk.Label(area, text='Only install software you trust. Installer packages may prompt for administrator access.').pack(anchor='w', pady=(6, 0))
        root.after(100, self.drain)

    def select_all(self, checked):
        for enabled, _, _ in self.rows.values(): enabled.set(checked)

    def selected_system(self):
        return detect_os() if self.system.get().startswith('Auto') else self.system.get()

    def selected_arch(self):
        if self.selected_system() == 'macOS':
            return 'universal'
        return detect_arch() if self.arch.get().startswith('Auto') else self.arch.get()

    def browse_folder(self):
        chosen = Path(self.download_dir.get()).expanduser()
        initial = chosen if chosen.is_dir() else chosen.parent
        if not initial.is_dir():
            initial = Path.home()
        folder = filedialog.askdirectory(parent=self.root, initialdir=str(initial),
                                         title='Select download folder')
        if folder:
            self.download_dir.set(folder)

    def update_button(self):
        system = self.selected_system()
        is_mac = system == 'macOS'
        if is_mac and self.arch.get() != 'Universal':
            self.last_non_mac_arch = self.arch.get()
            self.arch.set('Universal')
        elif not is_mac and self.arch.get() == 'Universal':
            self.arch.set(self.last_non_mac_arch)
        self.archbox.config(state='disabled' if is_mac else 'readonly')
        self.arch_label.config(state='disabled' if is_mac else 'normal')
        standalone = self.standalone.get()
        if standalone and not self.was_standalone:
            self.last_installer_linuxfmt = self.linuxfmt.get()
            self.linuxfmt.set('AppImage')
        elif not standalone and self.was_standalone:
            self.linuxfmt.set(self.last_installer_linuxfmt)
        self.was_standalone = standalone
        self.linuxfmtbox.config(values=['AppImage', 'Tarball'] if standalone else
                                ['DEB', 'RPM', 'AppImage', 'Flatpak', 'Tarball'])
        is_linux = system == 'Linux'
        self.linuxfmtbox.config(state='readonly' if is_linux else 'disabled')
        self.linuxfmt_label.config(state='normal' if is_linux else 'disabled')
        cross = system != detect_os() or (not is_mac and self.selected_arch() != detect_arch())
        if cross and not self.download_only.get():
            self.download_only.set(True)
        directory_enabled = self.download_only.get() or cross
        self.dir_entry.config(state='normal' if directory_enabled else 'disabled')
        self.browse_button.config(state='normal' if directory_enabled else 'disabled')
        self.desktop_box.config(state='disabled' if directory_enabled else 'normal')
        if directory_enabled and self.desktop_icons.get():
            self.desktop_icons.set(False)
        naming_state = 'normal' if self.desktop_icons.get() and not directory_enabled else 'disabled'
        for control in (self.shortcut_name_program, self.shortcut_name_similar,
                        self.shortcut_naming_label):
            control.config(state=naming_state)
        if is_mac:
            self.package_note.set('macOS: universal DMG (.app)')
        elif standalone:
            self.package_note.set('Windows: portable ZIP' if system == 'Windows' else 'Linux: AppImage / tarball')
        else:
            self.package_note.set('Windows: MSI installer' if system == 'Windows' else 'Linux: selected format')
        self.go.config(text='Download selected' if self.download_only.get() or cross else 'Download and install selected')

    def emit(self, kind, *args): self.messages.put((kind, args))

    def log(self, line): self.emit('log', line)

    def drain(self):
        try:
            while True:
                kind, args = self.messages.get_nowait()
                if kind == 'log':
                    self.logbox.configure(state='normal')
                    self.logbox.insert('end', args[0] + '\n')
                    self.logbox.see('end')
                    self.logbox.configure(state='disabled')
                elif kind == 'versions':
                    app, rels = args
                    self.releases[app] = rels
                    choices = ['Latest stable'] + [r['tag_name'] for r in rels]
                    self.rows[app][2].config(values=choices)
                elif kind == 'error': messagebox.showerror('Installer', args[0])
                elif kind == 'done':
                    self.busy = False
                    self.refresh.config(state='normal')
                    self.go.config(state='normal')
        except queue.Empty: pass
        self.root.after(100, self.drain)

    def work(self, fn):
        if self.busy: return
        self.busy = True
        self.refresh.config(state='disabled')
        self.go.config(state='disabled')
        def wrapper():
            try: fn()
            except Exception as exc: self.emit('error', str(exc))
            finally: self.emit('done')
        threading.Thread(target=wrapper, daemon=True).start()

    def load_versions(self):
        def task():
            for app in APPS:
                try:
                    rels = get_releases(app)
                    self.emit('versions', app, rels)
                    self.log(f'{app}: found {len(rels)} releases')
                except Exception as e: self.log(f'{app}: cannot fetch versions: {e}')
        self.work(task)

    def start(self):
        selected = [(app, version.get()) for app, (enabled, version, _) in self.rows.items() if enabled.get()]
        if not selected:
            messagebox.showinfo('Installer', 'Select at least one app.')
            return
        system, arch, fmt = self.selected_system(), self.selected_arch(), self.linuxfmt.get()
        standalone, create_icons = self.standalone.get(), self.desktop_icons.get()
        include_similar = self.shortcut_naming.get() == 'similar'
        only = self.download_only.get() or system != detect_os() or (system != 'macOS' and arch != detect_arch())
        if arch == 'unknown':
            messagebox.showerror('Unsupported architecture', 'Select x64 or arm64 manually.')
            return
        if only and not self.download_dir.get().strip():
            messagebox.showerror('Download folder', 'Choose a download folder first.')
            return
        destination = Path(self.download_dir.get().strip()).expanduser() if only else None
        if destination is not None and destination.exists() and not destination.is_dir():
            messagebox.showerror('Download folder', 'The download destination is not a folder.')
            return
        if not only and not messagebox.askyesno('Confirm installation',
            f'Download and install {len(selected)} app(s) for {system} {arch}?\n'
            f'Package type: {"Standalone / portable" if standalone else "Standard"}\n'
            f'Desktop shortcuts: {"Yes (with similar app)" if include_similar and create_icons else "Yes (program name)" if create_icons else "No"}\n'
            'Only install software you trust.'):
            return
        def task():
            good, bad = 0, 0
            if only:
                destination.mkdir(parents=True, exist_ok=True)
            for app, choice in selected:
                try:
                    rels = self.releases.get(app) or get_releases(app)
                    release = latest_release(rels) if choice == 'Latest stable' else next((r for r in rels if r['tag_name'] == choice), None)
                    if not release: raise RuntimeError('Release not found')
                    asset = choose_asset(release, system, arch, fmt, standalone)
                    if not asset:
                        raise RuntimeError(f'No {"standalone" if standalone else "standard"} '
                                           f'{system} {arch} {fmt if system == "Linux" else ""} '
                                           f'asset in {release["tag_name"]}')
                    self.log(f'[{app}] {release["tag_name"]}: downloading {asset["name"]}')
                    if only:
                        path = download(asset, destination, self.log)
                        self.log(f'[{app}] Download saved: {path}')
                    else:
                        # A temporary directory ensures installer packages are
                        # deleted after installation (including failed attempts).
                        with tempfile.TemporaryDirectory(prefix=f'craftapps-{app}-') as temp_dir:
                            path = download(asset, Path(temp_dir), self.log)
                            self.log(f'[{app}] Installing...')
                            {'Windows': install_windows, 'macOS': install_macos, 'Linux': install_linux}[system](
                                app, path, self.log, desktop=create_icons,
                                shortcut_label=shortcut_name(app, include_similar))
                        self.log(f'[{app}] Temporary installation package deleted')
                    good += 1
                    self.log(f'[{app}] SUCCESS')
                except Exception as exc:
                    bad += 1
                    self.log(f'[{app}] FAILED: {exc}')
            self.log(f'Finished. Successful: {good}; failed: {bad}')
        self.work(task)

if __name__ == '__main__':
    if sys.version_info < (3, 9):
        raise SystemExit('Python 3.9 or newer required')
    root = tk.Tk()
    InstallerUI(root)
    root.mainloop()