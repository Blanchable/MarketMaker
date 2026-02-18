"""Log viewer widget – scrollable text area with colored ERROR/WARN lines."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogViewer(QWidget):
    """Scrollable log viewer with color-coded lines."""

    _MAX_LINES = 5000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(self._MAX_LINES)
        self._text.setStyleSheet(
            "QPlainTextEdit {"
            "  background-color: #1e1e2e;"
            "  color: #cdd6f4;"
            "  font-family: 'Consolas', 'Courier New', monospace;"
            "  font-size: 12px;"
            "  border: 1px solid #45475a;"
            "  border-radius: 4px;"
            "  padding: 4px;"
            "}"
        )

        self._clear_btn = QPushButton("Clear View")
        self._clear_btn.setFixedWidth(100)
        self._clear_btn.clicked.connect(self._text.clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text)
        layout.addWidget(self._clear_btn, alignment=Qt.AlignRight)

    @Slot(str)
    def append_line(self, line: str) -> None:
        """Append a line with color based on content."""
        fmt = QTextCharFormat()

        upper = line.upper()
        if "ERROR" in upper or "CRITICAL" in upper:
            fmt.setForeground(QColor("#f38ba8"))  # red
        elif "WARN" in upper:
            fmt.setForeground(QColor("#fab387"))  # orange
        elif "KILL SWITCH" in upper:
            fmt.setForeground(QColor("#f38ba8"))
            fmt.setFontWeight(700)
        elif "SNIPER" in upper:
            fmt.setForeground(QColor("#a6e3a1"))  # green
        elif "FILL" in upper:
            fmt.setForeground(QColor("#89b4fa"))  # blue
        else:
            fmt.setForeground(QColor("#cdd6f4"))  # default

        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(line + "\n", fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()
