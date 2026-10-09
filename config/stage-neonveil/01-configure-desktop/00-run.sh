#!/bin/bash -e

install -D -m 0644 files/autologin.conf \
	"${ROOTFS_DIR}/etc/systemd/system/getty@tty1.service.d/autologin.conf"
install -D -m 0644 files/bash_profile \
	"${ROOTFS_DIR}/home/${FIRST_USER_NAME}/.bash_profile"
MYOS_VERSION="$(cat files/release/VERSION)"
install -D -m 0644 files/xinitrc \
	"${ROOTFS_DIR}/usr/share/neonveil/xinitrc"
install -d -m 0755 \
	"${ROOTFS_DIR}/usr/share/neonveil/releases/${MYOS_VERSION}"
cp -R files/release/. \
	"${ROOTFS_DIR}/usr/share/neonveil/releases/${MYOS_VERSION}/"
chown -R root:root \
	"${ROOTFS_DIR}/usr/share/neonveil/releases/${MYOS_VERSION}"
ln -s "releases/${MYOS_VERSION}" \
	"${ROOTFS_DIR}/usr/share/neonveil/current"
install -D -m 0755 files/myos-update \
	"${ROOTFS_DIR}/usr/bin/myos-update"
install -D -m 0644 files/myos-update-config.json \
	"${ROOTFS_DIR}/etc/myos-update/config.json"
install -D -m 0644 files/myos-update.rules \
	"${ROOTFS_DIR}/etc/polkit-1/rules.d/50-myos-update.rules"
rm -f "${ROOTFS_DIR}/etc/xdg/autostart/piwiz.desktop"

on_chroot <<EOF
set -eu
DEBIAN_FRONTEND=noninteractive apt-get purge -y cloud-init rpi-cloud-init-mods
usermod --shell /bin/bash "${FIRST_USER_NAME}"
getent group gpio >/dev/null || groupadd gpio
usermod -a -G gpio "${FIRST_USER_NAME}"
getent group sudo >/dev/null || groupadd sudo
usermod -a -G sudo "${FIRST_USER_NAME}"
printf '%s\n' "${FIRST_USER_NAME} ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/010-${FIRST_USER_NAME}-nopasswd"
chown root:root "/etc/sudoers.d/010-${FIRST_USER_NAME}-nopasswd"
chmod 0440 "/etc/sudoers.d/010-${FIRST_USER_NAME}-nopasswd"
chown "${FIRST_USER_NAME}:${FIRST_USER_NAME}" "/home/${FIRST_USER_NAME}/.bash_profile"
systemctl enable getty@tty1.service
systemctl enable NetworkManager.service bluetooth.service
EOF
