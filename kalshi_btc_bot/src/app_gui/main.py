"""Entry point for the Kalshi BTC Bot desktop application."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app_gui.widgets import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Kalshi BTC Bot")
    app.setOrganizationName("KalshiBot")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
