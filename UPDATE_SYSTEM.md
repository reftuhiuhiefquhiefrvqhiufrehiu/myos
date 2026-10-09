# NeonVeil update system

NeonVeil downloads versioned desktop/application bundles from GitHub Releases. It
does not execute scripts from a repository checkout. A release bundle contains
the `VERSION`, `apps/`, `desktop/`, and `update_manager/` files only. NeonVeil
extracts regular files into a new version directory, validates the contents,
and atomically changes the `current` symlink. The old version stays installed
until an administrator deliberately removes it.

The updater is designed for Raspberry Pi 4 ARM64 and the current
Raspberry Pi OS Lite (Debian Trixie) image. It does not update the Linux kernel,
boot firmware, Debian packages, or files outside `/usr/share/neonveil`; those
changes still require a new system image. A first image built with the new
versioned install layout is required to enable subsequent in-place desktop
updates on an older flat-layout installation.

## Repository and update source

The default source is configured centrally in
`update_manager/core.py` and in the image's
`/etc/myos-update/config.json`:

```json
{
  "repository": "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos"
}
```

Use a public GitHub repository with Releases enabled. The repository linked for
this project is currently empty; publish the project branch and the initial
release before expecting a Pi to find an update. For a private repository the
unauthenticated updater cannot download releases.

Changing the default repository requires changing the source constant and the
image configuration together. The updater accepts only HTTPS GitHub release
asset URLs belonging to the configured owner/repository. It does not download
or execute arbitrary files from the default branch.

## Versions and channels

`VERSION` at the repository root is the single installed NeonVeil version. Use
Semantic Versioning:

| Channel | Version example | GitHub release |
| --- | --- | --- |
| Stable | `1.4.0` | Published release, not marked prerelease |
| Beta | `1.5.0-beta.1` | Published prerelease with `-beta` in the tag |
| Developer | `1.6.0-dev.1`, `1.6.0-rc.1` | Published prerelease |

Release tags use the `v` prefix, for example `v1.4.0`; the tag without `v`
must exactly match `VERSION`. Stable users see only stable releases. Beta users
see stable and beta releases. Developer users see stable releases and all
prereleases.

The selected channel and automatic-update preferences are per Linux user and
persist in `~/.config/myos/update.json`. The defaults are Stable, automatic
checking off, automatic downloading off, and automatic installation off.
Installation can be opted into in Settings; an update notification is shown
before an opted-in background installation. A reboot is always a separate,
manual action.

## Preparing and publishing a release

1. Update `VERSION` to the new Semantic Version and update the NeonVeil source.
2. Run the relevant tests (the release workflow runs the complete test suite).
3. Create a GitHub Release whose tag matches the new version and whose release
   notes contain the user-facing changelog.
4. Mark Stable releases as non-prerelease. Mark Beta and Developer releases as
   prereleases; include `-beta`, `-dev`, `-alpha`, or `-rc` in the version.
5. Publish the release. The `Build MyOS update` GitHub Actions workflow checks
   the tag and version, runs tests, creates the ARM64 archive and its metadata,
   and attaches both files to the release.

To build the release assets locally instead:

```sh
python3 scripts/package_update.py \
  --channel stable \
  --changelog "Neue Funktionen und Fehlerbehebungen."
```

For prereleases use `--channel beta` or `--channel developer` and make sure the
version suffix agrees with that channel. The output is written to
`build/releases/`:

- `MyOS-<version>-arm64.tar.gz`
- `myos-update.json`

The metadata records the version, channel, release date, changelog, architecture,
platform, exact release asset URL, byte size, and SHA-256 digest. Do not publish
the archive without the matching metadata file.

## Download and security checks

The client uses HTTPS for the GitHub Releases API and release assets. It checks
that the tag and manifest agree, the package is an ARM64 Raspberry Pi 4 bundle,
the size is bounded and matches GitHub's release metadata, the asset URL belongs
to the configured repository, and the downloaded SHA-256 matches the manifest
(and GitHub's asset digest when the API supplies it). An interrupted or
truncated download is discarded. A failed verification prevents installation.

Installation requires the exact `/usr/bin/myos-update` Polkit action installed
by the image. The privileged CLI accepts only `install` and `rollback` through
Polkit; checks and downloads remain unprivileged so a root process never writes
an archive into a user-controlled cache directory. Archive extraction rejects
absolute paths, `..`, links, devices, and other non-regular file types. The
manager extracts only into a temporary directory under the releases directory;
it does not run package hooks or shell scripts. Once staged, it changes
`current` with an atomic symlink replacement. If staging or activation fails,
the old `current` link is left in place.

SHA-256 detects corruption and mismatched release assets; by itself it is not
an independent publisher signature. Trust is anchored in HTTPS and the
maintainers' access to the configured GitHub repository. Protect GitHub
accounts, release permissions, branch protections, and Actions permissions.

Before switching versions, NeonVeil archives only its settings/profile locations:

- `~/.config/neonveil`
- `~/.config/myos`
- `~/.local/share/MyOS`

Personal documents, downloads, pictures, music, and the Trash are not removed
or overwritten by update or rollback.
The privileged backup is kept root-only under
`/var/lib/myos-update/backups/<Linux-user-id>/`. Local update history and status
are also recorded under `/var/lib/myos-update/` and remain readable by the
desktop. Non-privileged technical logs are under
`~/.local/state/myos/update/`; privileged operation logs use
`/var/log/myos-update.log`.

## User interface and terminal

Open **NeonVeil Update Manager** from the Start menu, or use
**Einstellungen → System · Updates** to select a channel and set optional
automation. If no eligible release is published, the manager displays
“NeonVeil ist auf dem neuesten Stand.” When the network is unavailable, it explains
that it could not check and leaves the desktop usable.

The CLI invokes the same Python update manager as the UI:

```sh
myos-update check
myos-update download
myos-update install
myos-update status
myos-update history
myos-update rollback
myos-update channel
myos-update channel stable
myos-update channel beta
myos-update channel developer
```

`install` and `rollback` request the image's narrowly scoped Polkit permission.
Installation checks the release again and re-verifies the cached archive before
activating it. Installed-update history is stored locally under
`/var/lib/myos-update/` and is readable by the desktop. Per-user status and
non-privileged technical logs are under `~/.local/state/myos/update/`;
privileged logs use `/var/log/myos-update.log`.

## Rollback and recovery

After an update, choose **Vorherige Version wiederherstellen…** in the Update
Manager or run `myos-update rollback`. Confirm the warning and restart the Pi.
Rollback atomically switches `current` to the highest previously installed
lower version. It does not delete the newer version, restore user documents, or
modify user preferences; those remain as they were.

The updater does not automatically reboot or automatically remove old releases.
Keep enough free SD-card space for the download, extracted staged version, and
the previous version. If power is lost during download or extraction, the
active symlink is unchanged. If power is lost after the atomic switch, boot the
Pi and use rollback if the new desktop is not healthy. Keep a known-good full
image as the recovery path for SD-card or base-OS failures.

## Testing

Run the focused updater tests:

```sh
PYTHONPATH=desktop:. QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_update_manager -v
```

The tests cover Semantic Version comparison, channel selection, no-release and
offline cases, URL validation, SHA-256 checking, interrupted/corrupt downloads,
unsafe archive paths, staged activation, configuration backups, history, and
rollback. The release workflow runs the complete NeonVeil test suite before
publishing package assets.

Before deploying a production release to devices, also verify it on a Raspberry
Pi 4 with a backed-up SD card: check, download, verify, install, reboot,
open the desktop, and perform a rollback. Automated tests cannot simulate
power loss or certify every SD card, kernel, or display configuration.
