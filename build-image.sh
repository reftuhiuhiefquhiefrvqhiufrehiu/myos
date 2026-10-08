#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BUILD_DIR="$ROOT_DIR/build"
PI_GEN_DIR="$BUILD_DIR/pi-gen"
PI_GEN_URL="https://github.com/RPi-Distro/pi-gen.git"
PI_GEN_COMMIT="d346cd53562b3c6b4f94c9155175f814258c456d"
CONFIG_FILE="$ROOT_DIR/config/pi-gen"
CUSTOM_STAGE_SOURCE="$ROOT_DIR/config/stage-neonveil"
CUSTOM_STAGE_DEST="$PI_GEN_DIR/stage-neonveil"
CUSTOM_EXPORT_DEST="$PI_GEN_DIR/export-neonveil"
SOURCE_IMAGE="MyOS-RPi4-neonveil.img"
OUTPUT_IMAGE="$BUILD_DIR/MyOS-RPi4.img"

for tool in git docker; do
	if ! command -v "$tool" >/dev/null 2>&1; then
		echo "Required command not found: $tool" >&2
		exit 1
	fi
done

if ! docker info >/dev/null 2>&1; then
	echo "Docker is not available. Start Docker Engine or Docker Desktop and retry." >&2
	exit 1
fi

mkdir -p "$BUILD_DIR"

if [ ! -d "$PI_GEN_DIR/.git" ]; then
	if [ -e "$PI_GEN_DIR" ]; then
		echo "Refusing to use $PI_GEN_DIR because it is not a pi-gen Git checkout." >&2
		exit 1
	fi
	git clone --filter=blob:none --branch arm64 --single-branch \
		"$PI_GEN_URL" "$PI_GEN_DIR"
fi

if [ "$(git -C "$PI_GEN_DIR" remote get-url origin)" != "$PI_GEN_URL" ]; then
	echo "Unexpected origin URL in $PI_GEN_DIR; refusing to build." >&2
	exit 1
fi

if [ -n "$(git -C "$PI_GEN_DIR" status --porcelain --untracked-files=all)" ]; then
	echo "Local changes found in $PI_GEN_DIR; refusing to overwrite them." >&2
	exit 1
fi

git -C "$PI_GEN_DIR" fetch --depth 1 origin "$PI_GEN_COMMIT"
git -C "$PI_GEN_DIR" checkout --detach "$PI_GEN_COMMIT"

cp "$CONFIG_FILE" "$PI_GEN_DIR/config"

if [ -e "$CUSTOM_STAGE_DEST" ]; then
	echo "Unexpected custom stage already exists at $CUSTOM_STAGE_DEST." >&2
	exit 1
fi
if [ -e "$CUSTOM_EXPORT_DEST" ]; then
	echo "Unexpected custom export configuration already exists at $CUSTOM_EXPORT_DEST." >&2
	exit 1
fi
cp -R "$CUSTOM_STAGE_SOURCE" "$CUSTOM_STAGE_DEST"
mkdir -p "$CUSTOM_STAGE_DEST/01-configure-desktop/files/release/desktop"
cp "$ROOT_DIR/desktop/main.py" "$ROOT_DIR/desktop/shell.py" \
	"$ROOT_DIR/desktop/taskbar.py" \
	"$ROOT_DIR/desktop/profiles.py" \
	"$ROOT_DIR/desktop/notifications.py" \
	"$ROOT_DIR/desktop/system_info.py" \
	"$ROOT_DIR/desktop/version.py" \
	"$CUSTOM_STAGE_DEST/01-configure-desktop/files/release/desktop/"
cp -R "$ROOT_DIR/apps" "$CUSTOM_STAGE_DEST/01-configure-desktop/files/release/apps"
cp -R "$ROOT_DIR/update_manager" \
	"$CUSTOM_STAGE_DEST/01-configure-desktop/files/release/update_manager"
cp "$ROOT_DIR/VERSION" "$CUSTOM_STAGE_DEST/01-configure-desktop/files/release/VERSION"
cp "$ROOT_DIR/scripts/myos-update" \
	"$CUSTOM_STAGE_DEST/01-configure-desktop/files/myos-update"
cp -R "$PI_GEN_DIR/export-image" "$CUSTOM_EXPORT_DEST"
touch "$CUSTOM_EXPORT_DEST/01-user-rename/SKIP"

BUILD_LINK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/neonveil-pigen.XXXXXX")"
cleanup() {
	rm -f "$BUILD_LINK_DIR/pi-gen"
	rmdir "$BUILD_LINK_DIR"
	rm -rf "$CUSTOM_STAGE_DEST"
	rm -rf "$CUSTOM_EXPORT_DEST"
}
trap cleanup EXIT
ln -s "$PI_GEN_DIR" "$BUILD_LINK_DIR/pi-gen"

(
	cd "$BUILD_LINK_DIR/pi-gen"
	./build-docker.sh
)

IMAGE_PATH="$(find "$PI_GEN_DIR/deploy" -maxdepth 1 -type f \
	-name "*-$SOURCE_IMAGE" -print | sort | tail -n 1)"
if [ -z "$IMAGE_PATH" ]; then
	echo "Expected image was not produced in $PI_GEN_DIR/deploy." >&2
	exit 1
fi

cp "$IMAGE_PATH" "$OUTPUT_IMAGE"
echo "Image ready: $OUTPUT_IMAGE"
