import importlib
import json
import os
import platform
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
GPIO_PINS = tuple(range(2, 28))


def run_command(
    runner: CommandRunner, arguments: list[str], timeout: float = 5
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            arguments,
            capture_output=True,
            text=True,
            env={**os.environ, "LANG": "C", "LC_ALL": "C"},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"{arguments[0]} ist nicht verfügbar: {error}") from error
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            message or f"{arguments[0]} wurde mit Status {result.returncode} beendet."
        )
    return result


def parse_nmcli_fields(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current.clear()
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    security: str
    signal: int
    connected: bool


class WifiManager:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or subprocess.run

    def networks(self) -> list[WifiNetwork]:
        result = run_command(
            self.runner,
            ["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL", "device", "wifi", "list", "--rescan", "yes"],
            timeout=12,
        )
        networks: dict[str, WifiNetwork] = {}
        for line in result.stdout.splitlines():
            values = parse_nmcli_fields(line)
            if len(values) != 4 or not values[1].strip():
                continue
            try:
                signal_strength = max(0, min(100, int(values[3])))
            except ValueError:
                signal_strength = 0
            network = WifiNetwork(
                values[1],
                values[2] or "Offen",
                signal_strength,
                values[0] == "*",
            )
            networks[network.ssid] = network
        return sorted(networks.values(), key=lambda item: (-item.signal, item.ssid.casefold()))

    def status(self) -> tuple[str, str | None]:
        result = run_command(
            self.runner,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
        )
        for line in result.stdout.splitlines():
            fields = parse_nmcli_fields(line)
            if len(fields) >= 4 and fields[1] == "wifi" and fields[2] == "connected":
                return "Verbunden", fields[3]
        return "Nicht verbunden", None

    def connect(self, ssid: str, password: str = "") -> str:
        if not ssid:
            raise ValueError("Wähle zuerst ein WLAN-Netzwerk aus.")
        arguments = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            arguments.extend(["password", password])
        result = run_command(self.runner, arguments, timeout=30)
        return result.stdout.strip() or f"Mit „{ssid}“ verbunden."

    def disconnect(self) -> str:
        status = run_command(
            self.runner,
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
        )
        for line in status.stdout.splitlines():
            fields = parse_nmcli_fields(line)
            if len(fields) >= 3 and fields[1] == "wifi" and fields[2] == "connected":
                result = run_command(
                    self.runner, ["nmcli", "device", "disconnect", fields[0]]
                )
                return result.stdout.strip() or "WLAN getrennt."
        return "Es besteht keine WLAN-Verbindung."


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool
    connected: bool


class BluetoothManager:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or subprocess.run

    def _run(self, *arguments: str, timeout: float = 6) -> str:
        return run_command(self.runner, ["bluetoothctl", *arguments], timeout).stdout

    def powered(self) -> bool:
        output = self._run("show")
        for line in output.splitlines():
            key, separator, value = line.strip().partition(":")
            if separator and key.strip() == "Powered":
                return value.strip().casefold() == "yes"
        raise RuntimeError("Bluetooth-Status konnte nicht ermittelt werden.")

    def set_powered(self, enabled: bool) -> None:
        self._run("power", "on" if enabled else "off")

    def devices(self, paired_only: bool = False) -> list[BluetoothDevice]:
        paired_output = self._run("devices", "Paired")
        all_output = self._run("devices")
        connected_output = self._run("devices", "Connected")
        paired = self._parse_devices(paired_output)
        devices = paired if paired_only else self._parse_devices(all_output)
        connected = self._parse_devices(connected_output)
        results = []
        for address, name in devices.items():
            results.append(
                BluetoothDevice(
                    address,
                    name,
                    paired_only or address in paired,
                    address in connected,
                )
            )
        return sorted(results, key=lambda device: device.name.casefold())

    @staticmethod
    def _parse_devices(output: str) -> dict[str, str]:
        devices: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) == 3 and parts[0] == "Device":
                devices[parts[1]] = parts[2]
        return devices

    def scan(self) -> list[BluetoothDevice]:
        self._run("--timeout", "4", "scan", "on", timeout=7)
        return self.devices()

    def connect(self, device: BluetoothDevice) -> str:
        if not device.paired:
            self._run("pair", device.address, timeout=30)
        return self._run("connect", device.address, timeout=20).strip()

    def disconnect(self, device: BluetoothDevice) -> str:
        return self._run("disconnect", device.address).strip()


class GpioController:
    def __init__(self, pin_factory: Callable[[int, str], object] | None = None) -> None:
        self._pin_factory = pin_factory or self._create_pin
        self._devices: dict[int, tuple[str, object]] = {}

    @staticmethod
    def _create_pin(pin: int, mode: str) -> object:
        try:
            gpiozero = importlib.import_module("gpiozero")
            exceptions = importlib.import_module("gpiozero.exc")
        except ImportError as error:
            raise RuntimeError(
                "GPIO-Bibliothek fehlt. Installiere python3-gpiozero und python3-rpi.gpio."
            ) from error

        try:
            if mode == "Ausgang":
                return gpiozero.DigitalOutputDevice(
                    pin, active_high=True, initial_value=False
                )
            return gpiozero.DigitalInputDevice(pin, pull_up=False)
        except exceptions.GPIOZeroError as error:
            raise RuntimeError(f"GPIO {pin} konnte nicht geöffnet werden: {error}") from error

    def configure(self, pin: int, mode: str) -> None:
        if pin not in GPIO_PINS:
            raise ValueError("Wähle einen BCM-GPIO-Pin von 2 bis 27.")
        if mode not in {"Eingang", "Ausgang"}:
            raise ValueError("Ungültiger GPIO-Modus.")
        existing = self._devices.get(pin)
        if existing is not None and existing[0] == mode:
            return
        self.release(pin)
        try:
            device = self._pin_factory(pin, mode)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(f"GPIO {pin} konnte nicht geöffnet werden: {error}") from error
        self._devices[pin] = (mode, device)

    def set_output(self, pin: int, high: bool) -> None:
        device = self._configured(pin, "Ausgang")[1]
        device.value = bool(high)

    def read_input(self, pin: int) -> bool:
        device = self._configured(pin, "Eingang")[1]
        return bool(device.value)

    def release(self, pin: int) -> None:
        existing = self._devices.pop(pin, None)
        if existing is not None:
            existing[1].close()

    def close(self) -> None:
        for pin in tuple(self._devices):
            self.release(pin)

    def _configured(self, pin: int, mode: str) -> tuple[str, object]:
        existing = self._devices.get(pin)
        if existing is None or existing[0] != mode:
            self.configure(pin, mode)
            existing = self._devices[pin]
        return existing


@dataclass(frozen=True)
class PiSnapshot:
    model: str
    cpu: str
    temperature: str
    memory: str
    operating_system: str
    kernel: str


class PiInfoProvider:
    @staticmethod
    def _read(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "").strip()
        except OSError:
            return None

    def snapshot(self) -> PiSnapshot:
        model = self._read("/proc/device-tree/model") or "Nicht verfügbar"
        cpu = "Nicht verfügbar"
        cpu_info = self._read("/proc/cpuinfo") or ""
        hardware = ""
        for line in cpu_info.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            normalized_key = key.strip().casefold()
            if normalized_key == "model name":
                cpu = value.strip()
                break
            if normalized_key == "hardware":
                hardware = value.strip()
        if cpu == "Nicht verfügbar" and hardware:
            cpu = {
                "BCM2711": "Broadcom BCM2711 · Quad-core ARM Cortex-A72",
                "BCM2837": "Broadcom BCM2837 · Quad-core ARM Cortex-A53",
                "BCM2712": "Broadcom BCM2712 · Quad-core ARM Cortex-A76",
            }.get(hardware, hardware)
        temperature = self._temperature()
        memory = self._memory()
        os_info = self._read("/etc/os-release") or ""
        os_values = {}
        for line in os_info.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                os_values[key] = value.strip().strip('"')
        operating_system = os_values.get("PRETTY_NAME", "Nicht verfügbar")
        return PiSnapshot(
            model,
            cpu,
            temperature,
            memory,
            operating_system,
            platform.release() or "Nicht verfügbar",
        )

    def _temperature(self) -> str:
        raw = self._read("/sys/class/thermal/thermal_zone0/temp")
        if raw is None:
            return "Nicht verfügbar"
        try:
            celsius = int(raw) / 1000
        except ValueError:
            return "Nicht verfügbar"
        if not 0 <= celsius <= 120:
            return "Nicht verfügbar"
        return f"{celsius:.1f} °C"

    def _memory(self) -> str:
        raw = self._read("/proc/meminfo")
        if not raw:
            return "Nicht verfügbar"
        values = {}
        try:
            for line in raw.splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.split()[0]) * 1024
            total = values["MemTotal"]
            available = values["MemAvailable"]
        except (ValueError, KeyError, IndexError):
            return "Nicht verfügbar"
        return (
            f"{(total - available) / 1024**3:.1f} GB verwendet von "
            f"{total / 1024**3:.1f} GB"
        )


class NetworkInfoProvider:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        connection_test: Callable[[str, int, float], object] | None = None,
    ) -> None:
        self.runner = runner or subprocess.run
        self.connection_test = connection_test or socket.create_connection

    def snapshot(self) -> dict[str, str]:
        result = run_command(
            self.runner, ["ip", "-j", "route", "show", "default"]
        )
        try:
            routes = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Die Standardroute konnte nicht gelesen werden.") from error
        if not isinstance(routes, list) or not routes:
            return {
                "ip": "Nicht verbunden",
                "ssid": "Nicht verbunden",
                "speed": "Nicht verfügbar",
                "status": "Keine Standardroute",
            }
        interface = routes[0].get("dev")
        if not isinstance(interface, str) or not interface:
            raise RuntimeError("Die aktive Netzwerkschnittstelle ist unbekannt.")
        address_result = run_command(
            self.runner, ["ip", "-j", "-4", "address", "show", "dev", interface]
        )
        try:
            addresses = json.loads(address_result.stdout)
            ip_address = next(
                address["local"]
                for device in addresses
                for address in device.get("addr_info", [])
                if address.get("scope") == "global" and isinstance(address.get("local"), str)
            )
        except (json.JSONDecodeError, StopIteration, TypeError, KeyError) as error:
            raise RuntimeError("Die lokale IP-Adresse konnte nicht ermittelt werden.") from error

        ssid = "Kabelgebunden"
        if interface.startswith(("wl", "wlan")):
            ssid = self._wifi_ssid(interface)
        speed = self._link_speed(interface)
        return {
            "ip": ip_address,
            "ssid": ssid,
            "speed": speed,
            "status": f"Verbunden über {interface}",
        }

    def _wifi_ssid(self, interface: str) -> str:
        result = run_command(
            self.runner, ["iwgetid", interface, "--raw"]
        )
        ssid = result.stdout.strip()
        return ssid or "SSID nicht verfügbar"

    @staticmethod
    def _link_speed(interface: str) -> str:
        try:
            speed = int(Path(f"/sys/class/net/{interface}/speed").read_text().strip())
        except (OSError, ValueError):
            return "Nicht verfügbar"
        return f"{speed} Mbit/s" if speed > 0 else "Nicht verfügbar"

    def test_internet(self, timeout: float = 3) -> tuple[bool, str]:
        try:
            connection = self.connection_test("1.1.1.1", 53, timeout)
        except OSError as error:
            return False, f"Internet nicht erreichbar: {error}"
        close = getattr(connection, "close", None)
        if callable(close):
            close()
        return True, "Internetverbindung erreichbar (TCP/DNS-Test)."


class RaspberryPiToolsWindow(QMainWindow):
    def __init__(
        self,
        wifi: WifiManager | None = None,
        bluetooth: BluetoothManager | None = None,
        gpio: GpioController | None = None,
        network: NetworkInfoProvider | None = None,
        pi_info: PiInfoProvider | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Raspberry-Pi-Werkzeuge")
        self.setMinimumSize(560, 420)
        self.resize(720, 560)
        self.wifi = wifi or WifiManager()
        self.bluetooth = bluetooth or BluetoothManager()
        self.gpio = gpio or GpioController()
        self.network = network or NetworkInfoProvider()
        self.pi_info = pi_info or PiInfoProvider()

        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel("Raspberry-Pi-Werkzeuge")
        heading.setObjectName("heading")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._wifi_tab(), "WLAN")
        self.tabs.addTab(self._bluetooth_tab(), "Bluetooth")
        self.tabs.addTab(self._gpio_tab(), "GPIO")
        self.tabs.addTab(self._network_tab(), "Netzwerk")
        self.tabs.addTab(self._pi_info_tab(), "Pi-Info")
        layout.addWidget(heading)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(content)
        self._refresh_bluetooth_status()
        self._refresh_wifi_status()
        self._refresh_pi_info()
        self._refresh_network()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_gpio_inputs)
        self.refresh_timer.start(750)

    def _wifi_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.wifi_status = QLabel("Verbindungsstatus wird gelesen …")
        self.wifi_status.setWordWrap(True)
        controls = QHBoxLayout()
        self.wifi_networks = QComboBox()
        self.wifi_networks.setMinimumWidth(260)
        self.scan_wifi_button = QPushButton("Netze suchen")
        self.scan_wifi_button.clicked.connect(self._scan_wifi)
        controls.addWidget(self.wifi_networks, 1)
        controls.addWidget(self.scan_wifi_button)
        self.wifi_password = QLineEdit()
        self.wifi_password.setPlaceholderText("WLAN-Passwort (bei Bedarf)")
        self.wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.connect_wifi_button = QPushButton("Verbinden")
        self.connect_wifi_button.clicked.connect(self._connect_wifi)
        self.disconnect_wifi_button = QPushButton("Trennen")
        self.disconnect_wifi_button.clicked.connect(self._disconnect_wifi)
        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_wifi_button)
        buttons.addWidget(self.disconnect_wifi_button)
        self.wifi_message = QLabel()
        self.wifi_message.setWordWrap(True)
        layout.addWidget(self.wifi_status)
        layout.addLayout(controls)
        layout.addWidget(self.wifi_password)
        layout.addLayout(buttons)
        layout.addWidget(self.wifi_message)
        layout.addStretch(1)
        return page

    def _bluetooth_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.bluetooth_status = QLabel()
        self.bluetooth_status.setWordWrap(True)
        buttons = QHBoxLayout()
        self.bluetooth_power_button = QPushButton("Bluetooth ein-/ausschalten")
        self.bluetooth_power_button.clicked.connect(self._toggle_bluetooth)
        self.bluetooth_scan_button = QPushButton("Geräte suchen")
        self.bluetooth_scan_button.clicked.connect(self._scan_bluetooth)
        buttons.addWidget(self.bluetooth_power_button)
        buttons.addWidget(self.bluetooth_scan_button)
        self.bluetooth_devices = QListWidget()
        actions = QHBoxLayout()
        self.bluetooth_connect_button = QPushButton("Verbinden")
        self.bluetooth_connect_button.clicked.connect(self._connect_bluetooth)
        self.bluetooth_disconnect_button = QPushButton("Trennen")
        self.bluetooth_disconnect_button.clicked.connect(self._disconnect_bluetooth)
        self.bluetooth_refresh_button = QPushButton("Bekannte Geräte")
        self.bluetooth_refresh_button.clicked.connect(self._refresh_bluetooth_devices)
        actions.addWidget(self.bluetooth_connect_button)
        actions.addWidget(self.bluetooth_disconnect_button)
        actions.addWidget(self.bluetooth_refresh_button)
        self.bluetooth_message = QLabel()
        self.bluetooth_message.setWordWrap(True)
        layout.addWidget(self.bluetooth_status)
        layout.addLayout(buttons)
        layout.addWidget(self.bluetooth_devices, 1)
        layout.addLayout(actions)
        layout.addWidget(self.bluetooth_message)
        return page

    def _gpio_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        warning = QLabel(
            "Achtung: GPIO arbeitet mit 3,3 V. Falsche Verdrahtung kann den Pi "
            "dauerhaft beschädigen. Niemals 5 V an GPIO anschließen oder Pins "
            "direkt kurzschließen. Ausgänge vor dem Anschließen externer Schaltungen "
            "prüfen. Nutzung auf eigene Gefahr."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background: #fff1d6; color: #6e3a00; border: 1px solid #d49a37; padding: 8px;"
        )
        self.gpio_pin = QComboBox()
        for pin in GPIO_PINS:
            self.gpio_pin.addItem(f"GPIO {pin}", pin)
        self.gpio_mode = QComboBox()
        self.gpio_mode.addItems(["Eingang", "Ausgang"])
        self.gpio_add_button = QPushButton("Pin hinzufügen")
        self.gpio_add_button.clicked.connect(self._add_gpio_pin)
        add_row = QHBoxLayout()
        add_row.addWidget(self.gpio_pin)
        add_row.addWidget(self.gpio_mode)
        add_row.addWidget(self.gpio_add_button)
        self.gpio_rows = QVBoxLayout()
        self.gpio_message = QLabel()
        self.gpio_message.setWordWrap(True)
        layout.addWidget(warning)
        layout.addLayout(add_row)
        layout.addLayout(self.gpio_rows, 1)
        layout.addWidget(self.gpio_message)
        layout.addStretch(1)
        return page

    def _network_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.network_values: dict[str, QLabel] = {}
        form = QFormLayout()
        for key, label in (
            ("ip", "IP-Adresse"),
            ("ssid", "WLAN-Name"),
            ("speed", "Verbindungsgeschwindigkeit"),
            ("status", "Netzwerkstatus"),
        ):
            value = QLabel("Wird ermittelt …")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self.network_values[key] = value
            form.addRow(label, value)
        buttons = QHBoxLayout()
        refresh_button = QPushButton("Aktualisieren")
        refresh_button.clicked.connect(self._refresh_network)
        self.internet_test_button = QPushButton("Internet testen")
        self.internet_test_button.clicked.connect(self._test_internet)
        buttons.addWidget(refresh_button)
        buttons.addWidget(self.internet_test_button)
        self.network_message = QLabel(
            "Der Test öffnet nur eine direkte TCP-Verbindung zu einem öffentlichen DNS-Endpunkt."
        )
        self.network_message.setWordWrap(True)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.network_message)
        layout.addStretch(1)
        return page

    def _pi_info_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.pi_values: dict[str, QLabel] = {}
        form = QFormLayout()
        for key, label in (
            ("model", "Modell"),
            ("cpu", "CPU"),
            ("temperature", "Temperatur"),
            ("memory", "Speicher"),
            ("operating_system", "Betriebssystem"),
            ("kernel", "Kernel"),
        ):
            value = QLabel("Wird ermittelt …")
            value.setWordWrap(True)
            self.pi_values[key] = value
            form.addRow(label, value)
        refresh_button = QPushButton("Aktualisieren")
        refresh_button.clicked.connect(self._refresh_pi_info)
        layout.addLayout(form)
        layout.addWidget(refresh_button, 0, Qt.AlignmentFlag.AlignRight)
        layout.addStretch(1)
        return page

    def _scan_wifi(self) -> None:
        self.scan_wifi_button.setEnabled(False)
        self.wifi_message.setText("Suche nach lokalen WLAN-Netzen …")
        try:
            networks = self.wifi.networks()
        except (RuntimeError, ValueError) as error:
            self.wifi_message.setText(f"WLAN-Suche fehlgeschlagen: {error}")
        else:
            current = self.wifi_networks.currentData()
            self.wifi_networks.clear()
            for network in networks:
                label = f"{network.ssid} · {network.signal}% · {network.security}"
                self.wifi_networks.addItem(label, network.ssid)
                index = self.wifi_networks.count() - 1
                if network.connected:
                    self.wifi_networks.setCurrentIndex(index)
            if not networks:
                self.wifi_message.setText("Keine WLAN-Netze gefunden.")
            else:
                self.wifi_message.setText(f"{len(networks)} WLAN-Netze gefunden.")
                if current:
                    index = self.wifi_networks.findData(current)
                    if index >= 0:
                        self.wifi_networks.setCurrentIndex(index)
        finally:
            self.scan_wifi_button.setEnabled(True)
            self._refresh_wifi_status()

    def _refresh_wifi_status(self) -> None:
        try:
            state, ssid = self.wifi.status()
        except RuntimeError as error:
            self.wifi_status.setText(f"WLAN-Dienst nicht verfügbar: {error}")
            return
        self.wifi_status.setText(
            f"Status: {state}" + (f" · Netzwerk: {ssid}" if ssid else "")
        )

    def _connect_wifi(self) -> None:
        ssid = self.wifi_networks.currentData()
        if not isinstance(ssid, str):
            self.wifi_message.setText("Wähle zuerst ein WLAN-Netzwerk aus.")
            return
        self.connect_wifi_button.setEnabled(False)
        try:
            message = self.wifi.connect(ssid, self.wifi_password.text())
        except (RuntimeError, ValueError) as error:
            self.wifi_message.setText(f"Verbindung fehlgeschlagen: {error}")
        else:
            self.wifi_password.clear()
            self.wifi_message.setText(message)
        finally:
            self.connect_wifi_button.setEnabled(True)
            self._refresh_wifi_status()

    def _disconnect_wifi(self) -> None:
        try:
            self.wifi_message.setText(self.wifi.disconnect())
        except RuntimeError as error:
            self.wifi_message.setText(f"Trennen fehlgeschlagen: {error}")
        self._refresh_wifi_status()

    def _refresh_bluetooth_status(self) -> None:
        try:
            powered = self.bluetooth.powered()
        except RuntimeError as error:
            self.bluetooth_status.setText(f"Bluetooth nicht verfügbar: {error}")
        else:
            self.bluetooth_status.setText(
                f"Bluetooth ist {'eingeschaltet' if powered else 'ausgeschaltet'}."
            )

    def _toggle_bluetooth(self) -> None:
        try:
            self.bluetooth.set_powered(not self.bluetooth.powered())
        except RuntimeError as error:
            self.bluetooth_message.setText(f"Bluetooth konnte nicht geändert werden: {error}")
        else:
            self.bluetooth_message.setText("Bluetooth-Status aktualisiert.")
        self._refresh_bluetooth_status()

    def _scan_bluetooth(self) -> None:
        self.bluetooth_scan_button.setEnabled(False)
        self.bluetooth_message.setText("Suche lokale Bluetooth-Geräte …")
        try:
            devices = self.bluetooth.scan()
        except RuntimeError as error:
            self.bluetooth_message.setText(f"Gerätesuche fehlgeschlagen: {error}")
        else:
            self._fill_bluetooth_devices(devices)
            self.bluetooth_message.setText(f"{len(devices)} Geräte gefunden.")
        finally:
            self.bluetooth_scan_button.setEnabled(True)
            self._refresh_bluetooth_status()

    def _refresh_bluetooth_devices(self) -> None:
        try:
            devices = self.bluetooth.devices(paired_only=True)
        except RuntimeError as error:
            self.bluetooth_message.setText(f"Geräteliste nicht verfügbar: {error}")
            return
        self._fill_bluetooth_devices(devices)
        self.bluetooth_message.setText(f"{len(devices)} bekannte Geräte.")

    def _fill_bluetooth_devices(self, devices: list[BluetoothDevice]) -> None:
        self.bluetooth_devices.clear()
        for device in devices:
            state = "verbunden" if device.connected else (
                "gekoppelt" if device.paired else "gefunden"
            )
            item = QListWidgetItem(
                f"{device.name}  ·  {device.address}  ·  {state}"
            )
            item.setData(Qt.ItemDataRole.UserRole, device)
            self.bluetooth_devices.addItem(item)

    def _selected_bluetooth_device(self) -> BluetoothDevice | None:
        item = self.bluetooth_devices.currentItem()
        device = item.data(Qt.ItemDataRole.UserRole) if item else None
        return device if isinstance(device, BluetoothDevice) else None

    def _connect_bluetooth(self) -> None:
        device = self._selected_bluetooth_device()
        if device is None:
            self.bluetooth_message.setText("Wähle zuerst ein Gerät aus.")
            return
        try:
            self.bluetooth_message.setText(
                self.bluetooth.connect(device) or f"Mit {device.name} verbunden."
            )
        except RuntimeError as error:
            self.bluetooth_message.setText(f"Verbindung fehlgeschlagen: {error}")
        self._refresh_bluetooth_status()
        self._refresh_bluetooth_devices()

    def _disconnect_bluetooth(self) -> None:
        device = self._selected_bluetooth_device()
        if device is None:
            self.bluetooth_message.setText("Wähle zuerst ein Gerät aus.")
            return
        try:
            self.bluetooth_message.setText(
                self.bluetooth.disconnect(device) or f"{device.name} getrennt."
            )
        except RuntimeError as error:
            self.bluetooth_message.setText(f"Trennen fehlgeschlagen: {error}")
        self._refresh_bluetooth_status()
        self._refresh_bluetooth_devices()

    def _add_gpio_pin(self) -> None:
        pin = self.gpio_pin.currentData()
        mode = self.gpio_mode.currentText()
        if not isinstance(pin, int):
            self.gpio_message.setText("Wähle einen GPIO-Pin aus.")
            return
        for index in range(self.gpio_rows.count()):
            row = self.gpio_rows.itemAt(index).widget()
            if row is not None and row.property("gpio_pin") == pin:
                self.gpio_message.setText(f"GPIO {pin} wird bereits verwaltet.")
                return
        row = QWidget()
        row.setProperty("gpio_pin", pin)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)
        title = QLabel(f"GPIO {pin} · {mode}")
        state = QLabel("—")
        state.setObjectName("gpioState")
        output_button = QPushButton("LOW")
        output_button.setEnabled(mode == "Ausgang")
        output_button.setCheckable(True)
        output_button.toggled.connect(
            lambda high, selected=pin, button=output_button:
            self._set_gpio_output(selected, high, button)
        )
        remove_button = QPushButton("Entfernen")
        remove_button.clicked.connect(
            lambda checked, selected=pin:
            self._remove_gpio_pin(selected) if isinstance(checked, bool) else None
        )
        layout.addWidget(title, 1)
        layout.addWidget(state)
        layout.addWidget(output_button)
        layout.addWidget(remove_button)
        row.setProperty("gpio_mode", mode)
        self.gpio_rows.addWidget(row)
        try:
            self.gpio.configure(pin, mode)
        except (RuntimeError, ValueError) as error:
            self.gpio_rows.removeWidget(row)
            row.deleteLater()
            self.gpio_message.setText(str(error))
            return
        state.setText("LOW" if mode == "Ausgang" else "LOW")
        self.gpio_message.setText(f"GPIO {pin} als {mode.casefold()} eingerichtet.")
        if mode == "Eingang":
            self._refresh_gpio_inputs()

    def _set_gpio_output(self, pin: int, high: bool, button: QPushButton) -> None:
        try:
            self.gpio.set_output(pin, high)
        except (RuntimeError, ValueError) as error:
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
            self.gpio_message.setText(str(error))
            return
        button.setText("HIGH" if high else "LOW")
        self.gpio_message.setText(f"GPIO {pin} auf {'HIGH' if high else 'LOW'} gesetzt.")
        row = self._gpio_row(pin)
        if row is not None:
            state = row.findChild(QLabel, "gpioState")
            if state is not None:
                state.setText(button.text())

    def _refresh_gpio_inputs(self) -> None:
        for index in range(self.gpio_rows.count()):
            row = self.gpio_rows.itemAt(index).widget()
            if row is None or row.property("gpio_mode") != "Eingang":
                continue
            pin = row.property("gpio_pin")
            state = row.findChild(QLabel, "gpioState")
            if not isinstance(pin, int) or state is None:
                continue
            try:
                state.setText("HIGH" if self.gpio.read_input(pin) else "LOW")
            except (RuntimeError, ValueError) as error:
                state.setText("Fehler")
                self.gpio_message.setText(str(error))

    def _remove_gpio_pin(self, pin: int) -> None:
        row = self._gpio_row(pin)
        if row is not None:
            self.gpio.release(pin)
            self.gpio_rows.removeWidget(row)
            row.deleteLater()

    def _gpio_row(self, pin: int) -> QWidget | None:
        for index in range(self.gpio_rows.count()):
            row = self.gpio_rows.itemAt(index).widget()
            if row is not None and row.property("gpio_pin") == pin:
                return row
        return None

    def _refresh_network(self) -> None:
        try:
            values = self.network.snapshot()
        except RuntimeError as error:
            for value in self.network_values.values():
                value.setText("Nicht verfügbar")
            self.network_message.setText(f"Netzwerkinformation nicht verfügbar: {error}")
            return
        for key, value in values.items():
            if key in self.network_values:
                self.network_values[key].setText(value)
        self.network_message.setText("Lokale Netzwerkinformation aktualisiert.")

    def _test_internet(self) -> None:
        self.internet_test_button.setEnabled(False)
        success, message = self.network.test_internet()
        self.network_message.setText(message)
        self.network_message.setStyleSheet(
            "color: #17634d;" if success else "color: #9a3f35;"
        )
        self.internet_test_button.setEnabled(True)

    def _refresh_pi_info(self) -> None:
        snapshot = self.pi_info.snapshot()
        for key, value in self.pi_values.items():
            value.setText(getattr(snapshot, key))

    def closeEvent(self, event) -> None:
        self.refresh_timer.stop()
        self.gpio.close()
        super().closeEvent(event)
