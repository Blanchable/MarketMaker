"""Entry point for the Kalshi BTC Bot desktop application."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure .env is loaded from the project root regardless of working directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

from dotenv import load_dotenv

load_dotenv(_ENV_FILE)

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
