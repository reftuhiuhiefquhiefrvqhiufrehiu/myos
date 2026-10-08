import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from apps.pi_tools.app import (
    BluetoothDevice,
    BluetoothManager,
    GPIO_PINS,
    GpioController,
    NetworkInfoProvider,
    PiInfoProvider,
    RaspberryPiToolsWindow,
    WifiManager,
    parse_nmcli_fields,
)


def completed(
    arguments: list[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


class FakePin:
    def __init__(self, value: bool = False) -> None:
        self.value = value
        self.closed = False

    def close(self) -> None:
        self.closed = True


class PiToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_nmcli_parser_preserves_escaped_colons(self) -> None:
        self.assertEqual(
            parse_nmcli_fields(r"*:My\:Home:WPA2:87"),
            ["*", "My:Home", "WPA2", "87"],
        )

    def test_wifi_scan_status_connect_and_disconnect_use_networkmanager(self) -> None:
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            if "wifi" in arguments and "list" in arguments:
                return completed(
                    arguments,
                    r"*:Home\:Office:WPA2:84" + "\n:Guest:--:35\n: :WPA2:22\n",
                )
            if arguments[-2:] == ["device", "status"] or arguments[-3:] == ["device", "status"]:
                return completed(
                    arguments,
                    "wlan0:wifi:connected:Home\\:Office\neth0:ethernet:connected:Wired\n",
                )
            if "disconnect" in arguments:
                return completed(arguments, "Device 'wlan0' successfully disconnected.\n")
            return completed(arguments, "Device 'wlan0' successfully activated.\n")

        manager = WifiManager(runner)
        networks = manager.networks()
        self.assertEqual([network.ssid for network in networks], ["Home:Office", "Guest"])
        self.assertTrue(networks[0].connected)
        self.assertEqual(manager.status(), ("Verbunden", "Home:Office"))
        self.assertIn("successfully activated", manager.connect("Home:Office", "s3cret"))
        self.assertIn("password", calls[-1])
        self.assertIn("s3cret", calls[-1])
        manager.disconnect()
        self.assertEqual(calls[-1], ["nmcli", "device", "disconnect", "wlan0"])

    def test_wifi_command_failures_are_reported(self) -> None:
        manager = WifiManager(
            lambda arguments, **_kwargs: completed(
                arguments, stderr="NetworkManager is not running", returncode=8
            )
        )
        with self.assertRaisesRegex(RuntimeError, "NetworkManager is not running"):
            manager.networks()
        with self.assertRaisesRegex(ValueError, "Wähle zuerst"):
            manager.connect("")

    def test_bluetooth_lists_paired_connected_devices_and_controls_adapter(self) -> None:
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[-1:] == ["show"]:
                return completed(arguments, "Controller AA:BB:CC (public)\n\tPowered: yes\n")
            if arguments[-2:] == ["devices", "Paired"]:
                return completed(arguments, "Device AA:BB:CC:DD:EE:FF Headphones\n")
            if arguments[-1:] == ["Connected"]:
                return completed(arguments, "Device AA:BB:CC:DD:EE:FF Headphones\n")
            if arguments[-1:] == ["devices"]:
                return completed(
                    arguments,
                    "Device AA:BB:CC:DD:EE:FF Headphones\n"
                    "Device 11:22:33:44:55:66 Keyboard\n",
                )
            return completed(arguments)

        manager = BluetoothManager(runner)
        self.assertTrue(manager.powered())
        devices = manager.devices(paired_only=True)
        self.assertEqual(len(devices), 1)
        headphones = next(device for device in devices if device.name == "Headphones")
        self.assertTrue(headphones.paired)
        self.assertTrue(headphones.connected)
        self.assertEqual(len(manager.devices()), 2)
        manager.set_powered(False)
        self.assertEqual(calls[-1], ["bluetoothctl", "power", "off"])
        self.assertEqual(
            manager.connect(BluetoothDevice("AA:BB", "buds", True, False)), ""
        )

    def test_bluetooth_scan_and_service_errors_are_visible(self) -> None:
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            if "scan" in arguments:
                return completed(arguments)
            if arguments[-2:] == ["devices", "Paired"]:
                return completed(arguments, "")
            if arguments[-1:] == ["Connected"]:
                return completed(arguments, "")
            if arguments[-1:] == ["devices"]:
                return completed(arguments, "Device 00:11:22:33:44:55 Mouse\n")
            return completed(arguments, stderr="No default controller available", returncode=1)

        manager = BluetoothManager(runner)
        devices = manager.scan()
        self.assertEqual(devices[0].name, "Mouse")
        self.assertEqual(calls[0], ["bluetoothctl", "--timeout", "4", "scan", "on"])

        unavailable = BluetoothManager(
            lambda arguments, **_kwargs: completed(
                arguments, stderr="No default controller available", returncode=1
            )
        )
        with self.assertRaisesRegex(RuntimeError, "No default controller"):
            unavailable.powered()

    def test_gpio_controller_manages_multiple_pins_modes_and_release(self) -> None:
        made = {}

        def factory(pin, mode):
            device = FakePin(pin == 4)
            made[(pin, mode)] = device
            return device

        controller = GpioController(factory)
        controller.configure(17, "Ausgang")
        controller.set_output(17, True)
        self.assertTrue(made[(17, "Ausgang")].value)
        self.assertTrue(controller.read_input(4))
        self.assertIn(27, GPIO_PINS)
        with self.assertRaises(ValueError):
            controller.configure(1, "Ausgang")
        controller.release(17)
        self.assertTrue(made[(17, "Ausgang")].closed)
        controller.close()
        self.assertTrue(made[(4, "Eingang")].closed)

    def test_pi_info_reads_local_model_cpu_temperature_memory_and_os(self) -> None:
        data = {
            "/proc/device-tree/model": "Raspberry Pi 4 Model B",
            "/proc/cpuinfo": "Hardware : BCM2711\n",
            "/sys/class/thermal/thermal_zone0/temp": "52340",
            "/proc/meminfo": "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\n",
            "/etc/os-release": 'PRETTY_NAME="Raspberry Pi OS"\n',
        }
        with patch.object(PiInfoProvider, "_read", side_effect=lambda path: data.get(path)), patch(
            "apps.pi_tools.app.platform.release", return_value="6.12-test"
        ):
            snapshot = PiInfoProvider().snapshot()
        self.assertEqual(snapshot.model, "Raspberry Pi 4 Model B")
        self.assertEqual(
            snapshot.cpu, "Broadcom BCM2711 · Quad-core ARM Cortex-A72"
        )
        self.assertEqual(snapshot.temperature, "52.3 °C")
        self.assertIn("2.0 GB verwendet", snapshot.memory)
        self.assertEqual(snapshot.operating_system, "Raspberry Pi OS")
        self.assertEqual(snapshot.kernel, "6.12-test")

    def test_network_info_reads_local_route_wifi_and_link_speed(self) -> None:
        calls = []

        def runner(arguments, **_kwargs):
            calls.append(arguments)
            if arguments[:4] == ["ip", "-j", "route", "show"]:
                return completed(arguments, '[{"dev":"wlan0","gateway":"192.168.1.1"}]')
            if arguments[:4] == ["ip", "-j", "-4", "address"]:
                return completed(
                    arguments,
                    '[{"addr_info":[{"local":"192.168.1.20","scope":"global"}]}]',
                )
            if arguments[0] == "iwgetid":
                return completed(arguments, "HomeNetwork\n")
            raise AssertionError(arguments)

        provider = NetworkInfoProvider(runner)
        with patch.object(NetworkInfoProvider, "_link_speed", return_value="72 Mbit/s"):
            values = provider.snapshot()
        self.assertEqual(values["ip"], "192.168.1.20")
        self.assertEqual(values["ssid"], "HomeNetwork")
        self.assertEqual(values["speed"], "72 Mbit/s")
        self.assertIn("wlan0", values["status"])

    def test_network_no_route_and_internet_test_are_explicit(self) -> None:
        provider = NetworkInfoProvider(
            lambda arguments, **_kwargs: completed(arguments, "[]"),
            connection_test=lambda *_args: (_ for _ in ()).throw(
                OSError("Network is unreachable")
            ),
        )
        self.assertEqual(provider.snapshot()["status"], "Keine Standardroute")
        self.assertFalse(provider.test_internet()[0])
        reachable = NetworkInfoProvider(
            connection_test=lambda *_args: SimpleNamespace(close=lambda: None)
        )
        self.assertTrue(reachable.test_internet()[0])
        unreachable = NetworkInfoProvider(
            connection_test=lambda *_args: (_ for _ in ()).throw(
                OSError("Network is unreachable")
            )
        )
        self.assertIn("Network is unreachable", unreachable.test_internet()[1])

    def test_tools_window_has_all_five_local_hardware_sections(self) -> None:
        class Wifi:
            def status(self):
                return "Nicht verbunden", None

        class Bluetooth:
            def powered(self):
                return False

        class Gpio:
            def close(self):
                pass

        class Network:
            def snapshot(self):
                return {
                    "ip": "Nicht verbunden",
                    "ssid": "Nicht verbunden",
                    "speed": "Nicht verfügbar",
                    "status": "Offline",
                }

            def test_internet(self):
                return False, "offline"

        window = RaspberryPiToolsWindow(
            wifi=Wifi(),
            bluetooth=Bluetooth(),
            gpio=Gpio(),
            network=Network(),
            pi_info=PiInfoProvider(),
        )
        self.assertEqual(
            [window.tabs.tabText(index) for index in range(window.tabs.count())],
            ["WLAN", "Bluetooth", "GPIO", "Netzwerk", "Pi-Info"],
        )
        self.assertEqual(window.wifi_status.text(), "Status: Nicht verbunden")
        warning = next(
            label
            for label in window.tabs.widget(2).findChildren(QLabel)
            if "3,3 V" in label.text()
        )
        self.assertIn("dauerhaft beschädigen", warning.text())
        window.close()


if __name__ == "__main__":
    unittest.main()
