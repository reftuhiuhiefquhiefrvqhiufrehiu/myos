# NeonVeil

NeonVeil is a lightweight desktop system for Raspberry Pi 4. The project is
being built in phases on a bootable Raspberry Pi OS Lite image.

## Phase 1: Linux base and image

### Linux base

The image is based on **64-bit Raspberry Pi OS Lite (Debian Trixie)** and is
built with Raspberry Pi's official [`pi-gen`](https://github.com/RPi-Distro/pi-gen)
project, using its `arm64` branch. `pi-gen` is pinned to a specific commit in
[`build-image.sh`](./build-image.sh) so the image recipe does not silently
change when the upstream branch moves.

This is a good fit for the Raspberry Pi 4: Raspberry Pi OS has the vendor
bootloader, firmware, and hardware support; Lite omits the regular desktop
environment; and the Debian base supports Python and Qt for later phases. The
build runs in pi-gen's privileged Docker builder, so a Debian build host is not
required. On macOS, install and start Docker Desktop first.

The build needs internet access to fetch pi-gen and Debian/Raspberry Pi OS
packages. Once written to an SD card, the system itself does not require a
network connection to boot. Package repositories are rolling upstream sources,
so pinning pi-gen makes the recipe repeatable, but does not guarantee
byte-for-byte identical images over time.

### Build the image

Requirements:

- Git
- Docker Engine on Linux, or Docker Desktop on macOS
- At least 25 GB of free disk space for the build workspace and image
- Internet access during the build

From the project directory:

```sh
./build-image.sh
# or
make image
```

The raw image will be written to `build/MyOS-RPi4.img`. The first build can
take a while. The pi-gen checkout is kept under `build/pi-gen`; its temporary
custom stage and export configuration are removed when the build exits.

## Phase 2: Automatic login and desktop

The custom pi-gen stage installs a minimal Xorg/Openbox session and the Qt 6
PySide6 Widgets runtime from Debian packages. At boot, systemd automatically
logs the local `neonveil` account into tty1; its login profile starts X and the
NeonVeil desktop. Openbox supplies native window decorations and minimize,
maximize, move, and close behavior. The desktop shell draws its own background
and starts with an informational welcome window. No full desktop environment
is installed.

The local account has no preset password and SSH remains disabled. The first
user (`neonveil`) is added to the `sudo` group and gets passwordless sudo, so
the desktop session can perform administrative tasks. Automatic console login
means anyone with physical access can use that local session and its sudo
rights; do not treat it as a secured multi-user device. The image bypasses
Raspberry Pi's first-boot user rename wizard so it can land directly on the
desktop. NeonVeil now offers a local profile chooser when its desktop starts. These desktop
profiles only personalize the NeonVeil session: they do not isolate Linux files,
provide separate Linux accounts, or replace operating-system authentication.
The image still automatically logs the configured Linux account into tty1.
The desktop background now has double-click shortcuts, a Start menu, a
bottom taskbar with open-window buttons, and a live clock. The taskbar stays
above other windows so it is always visible. The Start menu
contains the built-in applications, Settings, shutdown, and restart. Shutdown
and restart ask for confirmation before invoking `systemctl`.

The desktop application lives in `desktop/`; the custom pi-gen stage copies it
into the versioned `/usr/share/neonveil/releases/<version>/` directory at build
time and activates it through `/usr/share/neonveil/current`. Boot and login
configuration lives in `config/stage-neonveil/01-configure-desktop/files/`.
For a quick headless UI smoke test on a Debian Trixie host with the matching
PySide6 packages installed:

```sh
sudo apt-get install python3-pyside6.qtwidgets qt6-qpa-plugins
PYTHONPATH=desktop \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -v
```

To build and test the complete boot image, run `./build-image.sh`, write it to
an SD card, and boot a Raspberry Pi 4 with a display and keyboard attached.

## Phase 3: Taskbar and Start menu

The desktop provides original Qt-style desktop shortcuts for Files, Text
Editor, Pictures, and Clock. Double-clicking a shortcut opens its window.
The bottom taskbar contains a Start menu button, buttons for open windows,
and a live 24-hour clock. Clicking an active taskbar button minimizes its
window; clicking it again restores the window.

The Start menu has a Programs submenu and direct Files and Settings entries.
Files, the text editor, and Pictures now launch working applications.
Shutdown and restart show a confirmation dialog, then request the corresponding
action from systemd. The wallpaper, icons, taskbar, and windows use NeonVeil
styling and standard system icons; no proprietary desktop assets are included.

## Phase 4: Files, text, and pictures

The **Dateien** application lists the current directory with folders first,
opens folders and files on double-click, and offers navigation, copy, move,
and recoverable deletion to the Papierkorb. Text files open in the built-in editor,
web-development files in Code Studio, images in Bilder, and MP3/OGG files in
the music player. Other file types are passed to the system's registered
application when available.

The **Texteditor** supports new, open, save, and save-as operations with UTF-8
text, standard keyboard shortcuts, and a prompt before discarding unsaved
changes. The **Bilder** viewer opens PNG, JPG, and JPEG files, cycles through
neighboring images with previous/next buttons or arrow keys, and supports
fullscreen with Escape to return.

These applications use PySide6/Qt and the standard Python library. JPEG Qt
image plugins are installed in the image. Their tests run together with the
desktop-shell tests:

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -v
```

## Phase 5: Time, terminal, browser, downloads, and Code Studio

The browser keeps its local offline start page and only accepts `http://` and `https://` addresses. A save dialog starts in `~/Downloads`; the Downloads button shows each download's state and byte progress and offers actions to open the file or its folder. The last 100 records and their final states are saved in the user's NeonVeil settings and restored when Browser is reopened. Unsafe server filenames are rejected, and existing files are given a collision-safe name rather than overwritten. Completed PNG/JPG downloads are handed directly to NeonVeil's own picture viewer, and downloaded images also open from the file manager. Failed, interrupted, and user-cancelled transfers have distinct visible states.

The integrated **Code Studio** is meant for local editing and preview. It supports HTML, CSS, JavaScript, Markdown, JSON, TypeScript/TSX files, and simple project folders, with line numbers and basic syntax coloring. Opening and saving source never starts it. Preview is an explicit action: HTML and JavaScript preview need confirmation because they can execute code; HTML/CSS that reference remote resources need network access. JavaScript output is displayed as text rather than inserted as markup. A JavaScript file can also be run with `node` only after confirmation. The app never shell-evaluates arbitrary user input; it invokes the runtime with an argument array, then displays stdout, stderr, and exit status.

The base image includes Debian Trixie's ARM64 [`nodejs`](https://packages.debian.org/trixie/arm64/nodejs) runtime and [`npm`](https://packages.debian.org/trixie/arm64/npm) package for JavaScript. The official Trixie listings verified `nodejs` 20.19.2 for arm64 and npm 9.2.0 (architecture-independent). This installs the general-purpose runtime and package manager, not any project's dependencies. For TypeScript/TSX or Next.js projects, those stacks normally need a project-local `npm install`, which requires network access and an actual project checkout. Code Studio can select a project folder, confirm and run its existing `npm run dev` script, stop the server, and open a detected local URL in NeonVeil Browser. If the selected project has no `node_modules` directory, Code Studio explicitly reminds you that `npm install` needs network access. The OS image does not bundle `node_modules`, Next.js, or a global TypeScript compiler.

The **Uhr** application shows the current time, a configurable countdown timer,
and a stopwatch with pause/resume and reset controls. It uses monotonic elapsed
time, so timer and stopwatch operation does not depend on wall-clock changes.

The **Terminal** window launches Debian's `qterminal` with a real interactive
user shell and lets you choose its working directory. It does not emulate a
shell inside a text box; commands run locally with the logged-in user's normal
permissions.

The **Browser** uses PySide6 QtWebEngine and offers an address bar, Back,
Forward, Reload, Home, and Downloads controls. Its built-in start page is
local, so the browser opens without internet access; loading websites and
transferring downloads require a network connection. Only HTTP and HTTPS
addresses are accepted from the address bar. QtWebEngine, `qterminal`,
`nodejs`, and `npm` are installed from Debian Trixie's repositories.

Tests for these applications run with the full desktop suite on Debian Trixie:

```sh
sudo apt-get install python3-pyside6.qtwidgets \
  python3-pyside6.qtwebenginewidgets python3-pyside6.qtmultimedia \
  gstreamer1.0-plugins-good qt6-qpa-plugins qterminal alsa-utils nodejs npm
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  QTWEBENGINE_DISABLE_SANDBOX=1 \
  python3 -m unittest discover -s tests -v
```

## Phase 6: Settings and system actions

Open **Einstellungen** from the Start menu to choose one of three built-in
teal/blue wallpaper palettes or select a local PNG/JPEG image. The selected
background is applied immediately and saved in the logged-in user's Qt
settings file (`~/.config/neonveil/neonveil.conf`); **Zurücksetzen** restores
the lagoon default.

The volume panel reads and changes an ALSA mixer control through `amixer`
(provided by `alsa-utils`). If the Pi has no available audio device or mixer,
the panel disables the control and explains why. Display modes come from the
active X11 display through `xrandr`; only modes the display reports are offered,
and failed mode changes are reported without claiming success. The About panel
identifies NeonVeil Phase 6 and its Raspberry Pi OS Lite / Debian Trixie base.

Shutdown and restart continue to require confirmation. If the `systemctl`
request cannot be run or returns an error, NeonVeil displays the command's
failure detail.

Settings use PySide6 and the standard Python library. The Phase 6 regression
tests can be run with the desktop shell tests on a Debian Trixie host:

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 tests/run_tests.py
```

`tests/run_tests.py` runs the same suite but exits without the fragile Qt
global teardown, which can otherwise abort the process after browser
(QtWebEngine) tests on some Linux/Qt combinations.

Runtime packages include `alsa-utils` for volume control. `xrandr` is supplied
by the existing `x11-xserver-utils` package. These are staged automatically by
the image build; run `./build-image.sh` only when you intend to generate a new
SD-card image.

## Phase 8: Desktop file interactions

Version 1.1.0 adds a unified local-file workflow. Double-clicking a file or
opening it with Enter routes common image, audio, web/code, and text formats
to their matching NeonVeil apps. NeonVeil `.desktop` links are read as local file or
application links; arbitrary shell `Exec` commands are never run.

The desktop shows items from `~/Desktop` alongside the built-in program icons.
Use its background context menu to add an application link, drag files onto the
desktop to create file links, or choose **Desktop-Verknüpfung erstellen** in
the file manager. Right-click an icon to open, inspect, remove an app icon, or
send a desktop file/link to the trash. Removed files remain recoverable until
the trash is emptied.

In the file manager, use arrows to select, Enter to open, Backspace to go up,
F2 to rename, Delete to move selected items to the trash, Ctrl+A to select all,
Ctrl+C/Ctrl+X/Ctrl+V to copy/cut/paste, and Ctrl+Shift+N to create a folder.
Drag items onto a folder to move them; hold Ctrl while dropping to copy. These
operations also work from selection-aware context menus. The trash supports
multi-item restore and confirmed permanent deletion, including a separately
confirmed **Papierkorb leeren** action.

## Phase 7: Profiles, appearance, notifications, and Start

At desktop startup, choose a local NeonVeil profile or create one with a username
and optional local PNG/JPEG avatar. **Abmelden** in the Start menu returns to
the chooser. Profiles are conveniences, not security boundaries: every profile
still uses the same automatically logged-in Linux account, home directory,
files, and permissions. No Linux autologin or session configuration is changed.

The **Erscheinungsbild** setting saves a light or dark palette alongside the
selected wallpaper. Wallpaper presets and local PNG/JPEG wallpaper selection
remain available. Dark mode applies a charcoal/teal palette to desktop
windows, menus, taskbar, and notification popups.

The Start menu includes searchable programs, an alphabetical app list, recent
programs, and per-app pin controls. **Benachrichtigungen** opens the locally
saved history (up to 100 entries); popups dismiss automatically and browser
image downloads report completion. **Über NeonVeil** shows the app version,
detected Raspberry Pi model and processor, CPU use, RAM use, and root-storage
use. Hardware values that cannot be read on the current system are shown as
unavailable rather than guessed. The nested **Ein/Aus** menu keeps the
confirmation step for shutdown and restart.

The profile, appearance, and notification records are stored in the user's
standard NeonVeil Qt settings file. To run their regression tests with the
desktop shell and settings tests:

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_desktop tests.test_phase6_settings \
  tests.test_desktop_features -v
```

### Write the image to an SD card

1. Insert an SD card suitable for your Pi 4. Writing the image erases the card.
2. Open **Raspberry Pi Imager**.
3. Choose **Choose Device** → **Raspberry Pi 4**.
4. Choose **Choose OS** → **Use custom** and select `build/MyOS-RPi4.img`.
5. Choose the SD card under **Choose Storage**, then select **Write**.
6. When writing finishes, eject the card safely, insert it into the Pi 4, and
   power on the Pi. It should automatically start the NeonVeil desktop.

### Updating

NeonVeil includes a GitHub Release-based **NeonVeil Update Manager**. It checks the
configured repository for ARM64 Raspberry Pi 4 releases, validates the archive
size and SHA-256 digest, stages the new desktop version beside the active one,
and switches the active version atomically. NeonVeil preferences and profiles are
backed up before installation; personal files are not included in or removed by
an update. A restart activates the new version. The previous version remains
available for rollback.

Open **Einstellungen → System · Updates** to choose the Stable, Beta, or
Developer channel and configure optional checks/downloads/installation, or open
**NeonVeil Update Manager** from the Start menu. Automatic installation is disabled
by default and requires explicit opt-in; it never restarts the Pi automatically.
The same update logic is available from the terminal with `myos-update`.

Updates from GitHub currently replace the NeonVeil desktop/application bundle.
Raspberry Pi OS, the Linux kernel, firmware, and Debian packages are not changed
by these bundles; use a newly built image for base-system changes or recovery.
See [`UPDATE_SYSTEM.md`](./UPDATE_SYSTEM.md) for repository setup, release
publishing, security checks, rollback, and testing.

## Phase 8: Dateien, Musik und Screenshots

The file manager searches for matching names recursively beneath the current
folder. Activate a result to open the file or enter the containing folder;
right-click an item and choose **Eigenschaften** for its size, detected type,
location, creation date when supplied by the filesystem, and modification
date. Linux filesystems that do not expose a birth time show that field as
unavailable instead of treating metadata-change time as creation time. Files
and folders whose name starts with a dot are hidden by default; toggle them
with the **Versteckte Dateien** button, the empty-area context menu, or
`Ctrl+H`. Hidden entries are also skipped during search until they are shown.

Deleting from the file manager moves the item into the local NeonVeil trash under
`~/.local/share/MyOS/Trash`. Open **Papierkorb** from the Start menu or file
manager to see its contents and total size or restore an item. A conflicting
restore name is preserved and the restored copy receives a numbered suffix.
**Papierkorb leeren** requires confirmation before permanent deletion.

The **Musik** app plays MP3 and OGG files in a simple playlist with playback,
seek, and volume controls. **Screenshot** captures the full screen and stores
sequential PNG files in `~/Pictures/Screenshots`; the **Druck** key also takes
a screenshot while NeonVeil is running. The image build includes Qt Multimedia
and GStreamer good plugins for common MP3/OGG decoding.

The file/media regression tests run alongside the existing phase 4 tests:

```sh
sudo apt-get install python3-pyside6.qtwidgets \
  python3-pyside6.qtmultimedia gstreamer1.0-plugins-good qt6-qpa-plugins
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_phase4_apps tests.test_file_media -v
```

## Phase 9: Raspberry-Pi-Werkzeuge

**Raspberry-Pi-Werkzeuge** in the Start menu provides local WLAN scanning and
connection through NetworkManager, Bluetooth power and device controls through
BlueZ, GPIO input/output testing through `gpiozero`, local IP/WLAN/link details,
and Raspberry Pi model, CPU, temperature, memory, OS, and kernel readings.
Internet checking is an explicit TCP connection test to a public DNS endpoint;
no cloud account or cloud API is used. The other device controls and system
information work against local Linux services and hardware.

The image enables NetworkManager and BlueZ and adds the desktop account to the
`gpio` group. GPIO is 3.3 V only: incorrect wiring, connecting 5 V, or shorting
pins can permanently damage the Raspberry Pi. The tool starts selected output
pins LOW and releases managed pins when closed. GPIO examples should only be
connected with suitable resistors and external-device protection.

These controls require the Raspberry Pi hardware and corresponding local
services. On another host, unavailable adapters, tools, permissions, or kernel
interfaces are reported in the app rather than simulated. The Pi-tool tests use
mocked local system interfaces and do not switch real network or GPIO state:

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_pi_tools -v
```

## Phase 10: Erweiterte Dateioperationen

Version 1.2.0 erweitert das Dateisystem-Verhalten des Dateimanagers und des
Desktops deutlich:

- **Ausschneiden, Kopieren und Einfügen** funktioniert für einzelne und mehrere
  Dateien und ganze Ordner. Ein ausgeschnittener Eintrag wird nach erfolgreichem
  Einfügen aus der Zwischenablage entfernt.
- **Drag & Drop** bewegt oder kopiert Elemente innerhalb eines Fensters, auf
  Unterordner und zwischen mehreren geöffneten Ordnerfenstern. Ohne Zusatztaste
  wird verschoben, mit gedrückter Strg-Taste kopiert.
- **Ziehen auf Desktop-Verknüpfungen**: Wird ein Element auf die Verknüpfung
  eines Ordners gezogen, landet es per Verschieben oder Kopieren direkt in
  diesem Ordner. Auf freier Desktopfläche bleibt das Erstellen einer
  Verknüpfung erhalten.
- **Fortschrittsfenster**: Große Kopier- und Verschiebevorgänge zeigen einen
  Fortschrittsdialog mit Abbrechen-Schaltfläche. Kleine Vorgänge laufen ohne
  Dialog, um den Ablauf nicht zu unterbrechen.
- **Konfliktdialoge**: Existiert am Ziel bereits ein Element, fragt NeonVeil nach
  **Ersetzen**, **Überspringen** oder **Umbenennen**. Über „Für alle folgenden
  Elemente anwenden“ gilt die Entscheidung für den restlichen Vorgang.

Fehler werden pro Element gesammelt und verständlich gemeldet, zum Beispiel
fehlende Berechtigungen oder nicht genügend Speicherplatz. Vor einem Vorgang
wird der freie Speicherplatz geprüft; reicht er nicht, wird die Übertragung
ohne Datenverlust abgelehnt. Ein Ordner kann nicht in sich selbst kopiert oder
verschoben werden. Die zentralen Übertragungsroutinen liegen in
`apps/file_manager/transfer.py` (reine Logik) und
`apps/file_manager/transfer_ui.py` (Dialoge).

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_file_transfer tests.test_z_desktop_interactions -v
```

## Version 1.4.0: Design system, polish, and robustness

Version 1.4.0 raises the whole desktop to a consistent, more polished level and
hardens it at runtime:

- **Central design system.** `desktop/theme.py` holds the light and dark colour
  tokens and generates one shared Qt stylesheet for windows, buttons, inputs,
  menus, scrollbars, tabs, sliders, progress bars, checkboxes, tooltips, and
  more. Light and dark appearance are generated from the same tokens instead of
  duplicated hex lists.
- **Richer visual layer.** The taskbar now uses a gradient surface, a glowing
  Start button, an accent underline for the active window, and a date next to
  the clock. The desktop background adds a soft neon bloom and vignette.
  Notification popups are rounded cards with a neon accent stripe.
- **Runtime hardening.** `desktop/logging_setup.py` writes a rotating log to
  `~/.local/share/NeonVeil/logs/neonveil.log`, routes Qt messages into the log,
  and installs an exception hook that records uncaught errors and shows a clear
  message instead of failing silently. The app name, version, and organisation
  are announced to Qt for correct metadata.
- **Robust image build.** `build-image.sh` now copies every `desktop/*.py`
  module into the image, so the design system and logging modules ship with the
  release and future modules cannot be forgotten.

```sh
PYTHONPATH=desktop:. \
  QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -v
```

## Project layout

```text
apps/                 Built-in Qt applications
apps/settings/        Wallpaper, audio, and display settings
apps/file_manager/    Recursive search, file properties, and recoverable trash
apps/file_manager/transfer.py    Copy/move engine with conflicts, progress, cancel
apps/file_manager/transfer_ui.py Progress and conflict dialogs
apps/music_player/    MP3/OGG playlist and playback
apps/screenshots/     Full-screen capture and Print-key shortcut
apps/pi_tools/        Local Raspberry Pi network, Bluetooth, GPIO and info tools
desktop/theme.py      Central light/dark colour tokens and Qt stylesheets
desktop/logging_setup.py  Rotating log file, Qt message bridge, exception hook
desktop/profiles.py   Local desktop profile chooser and store
desktop/notifications.py  Popup notifications and history
desktop/system_info.py    NeonVeil version and live hardware readings
apps/code_studio/     Local source editor, preview, and project runner
desktop/              PySide6 desktop application
settings/             Reserved for system-level configuration
assets/icons/         Original project icons
assets/wallpapers/    Original project wallpapers
config/               pi-gen configuration and custom image stage
scripts/              Supporting scripts
tests/                Automated desktop and app tests
build/                Generated image, pi-gen checkout, and build cache
build-image.sh        Reproducible image-build entry point
Makefile              Convenience build target
```
