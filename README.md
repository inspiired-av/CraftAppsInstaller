# Craft Apps Installer

A cross-platform graphical downloader and installer for the [storytold](https://github.com/storytold) Craft applications: PhotoCraft, LightCraft, FilmCraft, EffectCraft, DesignCraft, PDFCraft, VectorCraft, WordCraft, and GridCraft.

This project is an independent downloader and is **not affiliated with, endorsed by, or maintained by storytold or the companies behind the comparable commercial applications**.

![Craft Apps Installer screenshot](assets/screenshot.png)

## Download

Download the latest standalone builds from [Releases](../../releases) when available. Recipients do not need to install Python.

| Platform | Release download |
| --- | --- |
| Windows (x64) | `CraftAppsInstaller-Windows.exe` |
| macOS (Intel) | `CraftAppsInstaller-macOS-Intel.zip` |
| macOS (Apple Silicon) | `CraftAppsInstaller-macOS-AppleSilicon.zip` |

On macOS, unzip the archive and launch `CraftAppsInstaller.app`. These initial builds are unsigned and may encounter macOS Gatekeeper or Windows SmartScreen warnings. Only run downloads you trust.

## Features

- Choose one or more Craft apps and optionally select a specific release (latest stable by default).
- Automatically detect the operating system and CPU architecture, with manual overrides for download-only use.
- Select normal installers or standalone/portable packages where release assets support them.
- Download only to a selected folder, or install automatically and remove temporary installer packages.
- Optionally create desktop shortcuts pointing to the installed applications, with either short program names or names that include comparable commercial apps.
- macOS universal packages and Linux DEB, RPM, AppImage, Flatpak, and tarball selection, subject to what each upstream release actually provides.

## Run from Python source

Python 3.9+ with Tkinter is required only to run the source directly:

```bash
python CraftAppsInstaller.py
```

On some Linux distributions, Tkinter must be installed separately.

## Automated builds and releases

The workflow in `.github/workflows/build.yml` creates a Windows x64 executable plus native macOS Intel and Apple Silicon application archives using PyInstaller.

- Pushes to `main` and manual workflow dispatch create downloadable **Actions artifacts**.
- Pushing a version tag starting with `v` (for example `v1.0.0`) builds all three targets and publishes a **GitHub Release** when all builds succeed.

Example release commands:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Find CI build logs and artifacts in the repository's **Actions** tab. Publishing requires GitHub Actions to be enabled, and release creation requires the workflow token's `contents: write` permission (granted to its release job).

Builds are not code-signed or notarized. Automatic installations can request operating-system administrator privileges. Windows and macOS behavior should be tested on each target platform before wide distribution.

## License

See [LICENSE](LICENSE) for the GNU General Public License v3.
