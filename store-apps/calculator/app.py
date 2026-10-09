from __future__ import annotations

import math
import re
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class SafeCalculator:
    """Evaluates basic arithmetic expressions safely without eval()."""

    TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[+\-*/%^()]")

    @classmethod
    def evaluate(cls, expression: str) -> float | int:
        expr = expression.replace("×", "*").replace("÷", "/")
        tokens = cls.TOKEN_RE.findall(expr)
        if not tokens:
            raise ValueError("Leerer Ausdruck")

        # Shunting-yard algorithm
        precedence = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 3}
        output_queue: list[str | float] = []
        op_stack: list[str] = []

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if re.match(r"^\d+(?:\.\d+)?$", token):
                output_queue.append(float(token))
            elif token in precedence:
                # Handle unary minus if at start or after another operator / '('
                if token == "-" and (i == 0 or tokens[i - 1] in precedence or tokens[i - 1] == "("):
                    if i + 1 < len(tokens) and re.match(r"^\d+(?:\.\d+)?$", tokens[i + 1]):
                        output_queue.append(-float(tokens[i + 1]))
                        i += 2
                        continue
                while (
                    op_stack
                    and op_stack[-1] in precedence
                    and precedence[op_stack[-1]] >= precedence[token]
                ):
                    output_queue.append(op_stack.pop())
                op_stack.append(token)
            elif token == "(":
                op_stack.append(token)
            elif token == ")":
                while op_stack and op_stack[-1] != "(":
                    output_queue.append(op_stack.pop())
                if not op_stack or op_stack[-1] != "(":
                    raise ValueError("Ungültige Klammern")
                op_stack.pop()
            else:
                raise ValueError(f"Unbekanntes Symbol: {token}")
            i += 1

        while op_stack:
            top = op_stack.pop()
            if top in ("(", ")"):
                raise ValueError("Ungültige Klammern")
            output_queue.append(top)

        # Evaluate RPN
        eval_stack: list[float] = []
        for item in output_queue:
            if isinstance(item, (int, float)):
                eval_stack.append(float(item))
            elif item in precedence:
                if len(eval_stack) < 2:
                    raise ValueError("Syntaxfehler")
                b = eval_stack.pop()
                a = eval_stack.pop()
                if item == "+":
                    res = a + b
                elif item == "-":
                    res = a - b
                elif item == "*":
                    res = a * b
                elif item == "/":
                    if b == 0:
                        raise ZeroDivisionError("Division durch Null")
                    res = a / b
                elif item == "%":
                    res = a % b
                elif item == "^":
                    res = math.pow(a, b)
                else:
                    raise ValueError("Unbekannter Operator")
                eval_stack.append(res)

        if len(eval_stack) != 1:
            raise ValueError("Syntaxfehler")

        res = eval_stack[0]
        if res.is_integer() and abs(res) < 1e15:
            return int(res)
        return round(res, 8)


class CalculatorWindow(QMainWindow):
    """Modern calculator application for NeonVeil."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Neon Calculator")
        self.setMinimumSize(320, 440)
        self.resize(360, 480)

        self._expression = ""
        self._history: list[str] = []

        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # Display section
        display_box = QWidget()
        display_box.setObjectName("calcDisplayBox")
        display_box.setStyleSheet(
            """
            QWidget#calcDisplayBox {
                background: rgba(0, 0, 0, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 10px;
            }
            """
        )
        disp_layout = QVBoxLayout(display_box)
        disp_layout.setContentsMargins(8, 8, 8, 8)
        disp_layout.setSpacing(4)

        self.history_label = QLabel("")
        self.history_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.history_label.setStyleSheet("color: #71858b; font-size: 13px;")
        disp_layout.addWidget(self.history_label)

        self.result_label = QLabel("0")
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        self.result_label.setFont(font)
        self.result_label.setStyleSheet("color: #35c9bd; font-size: 28px; font-weight: bold;")
        disp_layout.addWidget(self.result_label)

        main_layout.addWidget(display_box)

        # Buttons grid
        grid = QGridLayout()
        grid.setSpacing(8)

        buttons = [
            ("C", 0, 0, "action"),
            ("(", 0, 1, "op"),
            (")", 0, 2, "op"),
            ("÷", 0, 3, "accent"),
            ("7", 1, 0, "num"),
            ("8", 1, 1, "num"),
            ("9", 1, 2, "num"),
            ("×", 1, 3, "accent"),
            ("4", 2, 0, "num"),
            ("5", 2, 1, "num"),
            ("6", 2, 2, "num"),
            ("-", 2, 3, "accent"),
            ("1", 3, 0, "num"),
            ("2", 3, 1, "num"),
            ("3", 3, 2, "num"),
            ("+", 3, 3, "accent"),
            ("±", 4, 0, "action"),
            ("0", 4, 1, "num"),
            (".", 4, 2, "num"),
            ("=", 4, 3, "accent_high"),
        ]

        for text, row, col, kind in buttons:
            btn = QPushButton(text)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            btn.setMinimumHeight(46)
            font = QFont()
            font.setPointSize(16)
            font.setBold(True if kind != "num" else False)
            btn.setFont(font)
            self._style_button(btn, kind)
            btn.clicked.connect(lambda checked=False, t=text: self._on_button_click(t))
            grid.addWidget(btn, row, col)

        main_layout.addLayout(grid)

    def _style_button(self, btn: QPushButton, kind: str) -> None:
        if kind == "accent_high":
            btn.setStyleSheet(
                """
                QPushButton {
                    background: #176c67;
                    color: #ffffff;
                    border: 1px solid #35c9bd;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 18px;
                }
                QPushButton:hover {
                    background: #247f79;
                }
                QPushButton:pressed {
                    background: #0f4e4a;
                }
                """
            )
        elif kind == "accent":
            btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(23, 108, 103, 0.25);
                    color: #35c9bd;
                    border: 1px solid rgba(53, 201, 189, 0.4);
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 17px;
                }
                QPushButton:hover {
                    background: rgba(23, 108, 103, 0.45);
                }
                QPushButton:pressed {
                    background: rgba(23, 108, 103, 0.6);
                }
                """
            )
        elif kind == "action":
            btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255, 255, 255, 0.08);
                    color: #e4ecee;
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 8px;
                    font-size: 15px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.15);
                }
                """
            )
        else:
            btn.setStyleSheet(
                """
                QPushButton {
                    background: rgba(255, 255, 255, 0.05);
                    color: #e4ecee;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    font-size: 16px;
                }
                QPushButton:hover {
                    background: rgba(255, 255, 255, 0.12);
                }
                QPushButton:pressed {
                    background: rgba(255, 255, 255, 0.2);
                }
                """
            )

    def _on_button_click(self, text: str) -> None:
        if text == "C":
            self._expression = ""
            self.history_label.setText("")
            self.result_label.setText("0")
        elif text == "±":
            if self._expression:
                if self._expression.startswith("-"):
                    self._expression = self._expression[1:]
                else:
                    self._expression = "-" + self._expression
                self.result_label.setText(self._expression)
        elif text == "=":
            self.calculate()
        else:
            self._expression += text
            self.result_label.setText(self._expression)

    def calculate(self) -> None:
        if not self._expression:
            return
        try:
            val = SafeCalculator.evaluate(self._expression)
            self.history_label.setText(f"{self._expression} =")
            self.result_label.setText(str(val))
            self._history.append(f"{self._expression} = {val}")
            self._expression = str(val)
        except ZeroDivisionError:
            self.result_label.setText("Teilung durch 0")
            self._expression = ""
        except Exception:
            self.result_label.setText("Fehler")
            self._expression = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        text = event.text()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.calculate()
        elif key == Qt.Key.Key_Escape:
            self._on_button_click("C")
        elif key == Qt.Key.Key_Backspace:
            if self._expression:
                self._expression = self._expression[:-1]
                self.result_label.setText(self._expression or "0")
        elif text in "0123456789.+-*/()":
            replace_map = {"*": "×", "/": "÷"}
            self._on_button_click(replace_map.get(text, text))
        else:
            super().keyPressEvent(event)
