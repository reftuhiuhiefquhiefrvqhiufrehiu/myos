from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


CONVERSIONS = {
    "Länge": {
        "Meter (m)": 1.0,
        "Kilometer (km)": 1000.0,
        "Zentimeter (cm)": 0.01,
        "Millimeter (mm)": 0.001,
        "Meilen (mi)": 1609.344,
        "Yards (yd)": 0.9144,
        "Fuß (ft)": 0.3048,
        "Zoll (in)": 0.0254,
    },
    "Masse / Gewicht": {
        "Kilogramm (kg)": 1.0,
        "Gramm (g)": 0.001,
        "Milligramm (mg)": 0.000001,
        "Tonnen (t)": 1000.0,
        "Pfund (lb)": 0.45359237,
        "Unzen (oz)": 0.028349523125,
    },
    "Datenspeicher": {
        "Byte (B)": 1.0,
        "Kilobyte (KB)": 1024.0,
        "Megabyte (MB)": 1024.0**2,
        "Gigabyte (GB)": 1024.0**3,
        "Terabyte (TB)": 1024.0**4,
    },
    "Geschwindigkeit": {
        "Kilometer pro Stunde (km/h)": 1.0,
        "Meter pro Sekunde (m/s)": 3.6,
        "Meilen pro Stunde (mph)": 1.609344,
        "Knoten (kn)": 1.852,
    },
    "Fläche": {
        "Quadratmeter (m²)": 1.0,
        "Quadratkilometer (km²)": 1_000_000.0,
        "Hektar (ha)": 10_000.0,
        "Quadratfuß (sq ft)": 0.092903,
        "Acres (ac)": 4046.8564224,
    },
}


class UnitConverterWindow(QMainWindow):
    """Modern unit converter application for NeonVeil."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Einheitenrechner")
        self.setMinimumSize(380, 420)
        self.resize(420, 460)

        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # Category
        cat_box = QHBoxLayout()
        cat_label = QLabel("Kategorie:")
        cat_label.setStyleSheet("font-weight: bold; color: #14596a; font-size: 14px;")
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(["Länge", "Masse / Gewicht", "Temperatur", "Datenspeicher", "Geschwindigkeit", "Fläche"])
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        cat_box.addWidget(cat_label)
        cat_box.addWidget(self.cat_combo, 1)
        main_layout.addLayout(cat_box)

        # Input Section
        in_group = QWidget()
        in_layout = QVBoxLayout(in_group)
        in_layout.setContentsMargins(0, 0, 0, 0)
        in_layout.setSpacing(6)

        in_title = QLabel("Von:")
        in_title.setStyleSheet("color: #71858b; font-size: 12px;")
        in_layout.addWidget(in_title)

        in_row = QHBoxLayout()
        self.in_input = QLineEdit("1")
        self.in_input.setPlaceholderText("Wert eingeben...")
        self.in_input.setStyleSheet(
            "font-size: 16px; padding: 8px; border: 1px solid #536a71; border-radius: 6px;"
        )
        self.in_input.textChanged.connect(self._convert)
        self.in_unit_combo = QComboBox()
        self.in_unit_combo.setStyleSheet("padding: 8px; font-size: 14px;")
        self.in_unit_combo.currentIndexChanged.connect(self._convert)

        in_row.addWidget(self.in_input, 2)
        in_row.addWidget(self.in_unit_combo, 3)
        in_layout.addLayout(in_row)
        main_layout.addWidget(in_group)

        # Swap button
        swap_box = QHBoxLayout()
        swap_box.addStretch(1)
        self.swap_btn = QPushButton("⇅ Einheiten tauschen")
        self.swap_btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(23, 108, 103, 0.2);
                color: #35c9bd;
                border: 1px solid #176c67;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(23, 108, 103, 0.4);
            }
            """
        )
        self.swap_btn.clicked.connect(self._swap_units)
        swap_box.addWidget(self.swap_btn)
        swap_box.addStretch(1)
        main_layout.addLayout(swap_box)

        # Output Section
        out_group = QWidget()
        out_layout = QVBoxLayout(out_group)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.setSpacing(6)

        out_title = QLabel("Nach:")
        out_title.setStyleSheet("color: #71858b; font-size: 12px;")
        out_layout.addWidget(out_title)

        out_row = QHBoxLayout()
        self.out_output = QLineEdit()
        self.out_output.setReadOnly(True)
        self.out_output.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #35c9bd; padding: 8px; border: 1px solid #176c67; border-radius: 6px;"
        )
        self.out_unit_combo = QComboBox()
        self.out_unit_combo.setStyleSheet("padding: 8px; font-size: 14px;")
        self.out_unit_combo.currentIndexChanged.connect(self._convert)

        out_row.addWidget(self.out_output, 2)
        out_row.addWidget(self.out_unit_combo, 3)
        out_layout.addLayout(out_row)
        main_layout.addWidget(out_group)

        # Copy and details
        btn_box = QHBoxLayout()
        self.copy_btn = QPushButton("Ergebnis kopieren")
        self.copy_btn.setStyleSheet(
            """
            QPushButton {
                background: #176c67;
                color: #ffffff;
                border: 1px solid #35c9bd;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #247f79;
            }
            """
        )
        self.copy_btn.clicked.connect(self._copy_result)
        btn_box.addWidget(self.copy_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #65b9ae; font-size: 12px;")
        btn_box.addWidget(self.status_label)
        btn_box.addStretch(1)
        main_layout.addLayout(btn_box)

        main_layout.addStretch(1)
        self._on_category_changed()

    def _on_category_changed(self) -> None:
        cat = self.cat_combo.currentText()
        self.in_unit_combo.blockSignals(True)
        self.out_unit_combo.blockSignals(True)
        self.in_unit_combo.clear()
        self.out_unit_combo.clear()

        if cat == "Temperatur":
            units = ["Grad Celsius (°C)", "Grad Fahrenheit (°F)", "Kelvin (K)"]
        else:
            units = list(CONVERSIONS.get(cat, {}).keys())

        self.in_unit_combo.addItems(units)
        self.out_unit_combo.addItems(units)
        if len(units) > 1:
            self.out_unit_combo.setCurrentIndex(1)

        self.in_unit_combo.blockSignals(False)
        self.out_unit_combo.blockSignals(False)
        self._convert()

    def _swap_units(self) -> None:
        in_idx = self.in_unit_combo.currentIndex()
        out_idx = self.out_unit_combo.currentIndex()
        self.in_unit_combo.blockSignals(True)
        self.out_unit_combo.blockSignals(True)
        self.in_unit_combo.setCurrentIndex(out_idx)
        self.out_unit_combo.setCurrentIndex(in_idx)
        self.in_unit_combo.blockSignals(False)
        self.out_unit_combo.blockSignals(False)
        self._convert()

    def _convert(self) -> None:
        text = self.in_input.text().strip().replace(",", ".")
        if not text:
            self.out_output.setText("")
            self.status_label.setText("")
            return
        try:
            val = float(text)
        except ValueError:
            self.out_output.setText("Ungültige Zahl")
            return

        cat = self.cat_combo.currentText()
        from_unit = self.in_unit_combo.currentText()
        to_unit = self.out_unit_combo.currentText()

        if cat == "Temperatur":
            result = self._convert_temp(val, from_unit, to_unit)
        else:
            table = CONVERSIONS.get(cat, {})
            from_factor = table.get(from_unit, 1.0)
            to_factor = table.get(to_unit, 1.0)
            base_val = val * from_factor
            result = base_val / to_factor

        if result.is_integer() and abs(result) < 1e12:
            formatted = f"{int(result)}"
        elif abs(result) < 0.0001 or abs(result) > 1e9:
            formatted = f"{result:.6e}"
        else:
            formatted = f"{round(result, 6):g}"

        self.out_output.setText(formatted)

    def _convert_temp(self, val: float, from_u: str, to_u: str) -> float:
        # Convert to Celsius first
        if "Celsius" in from_u:
            c = val
        elif "Fahrenheit" in from_u:
            c = (val - 32.0) * 5.0 / 9.0
        elif "Kelvin" in from_u:
            c = val - 273.15
        else:
            c = val

        # From Celsius to target
        if "Celsius" in to_u:
            return c
        elif "Fahrenheit" in to_u:
            return (c * 9.0 / 5.0) + 32.0
        elif "Kelvin" in to_u:
            return c + 273.15
        return c

    def _copy_result(self) -> None:
        res = self.out_output.text()
        if res and res != "Ungültige Zahl":
            clipboard = QGuiApplication.clipboard()
            if clipboard:
                clipboard.setText(res)
                self.status_label.setText("Kopiert!")
