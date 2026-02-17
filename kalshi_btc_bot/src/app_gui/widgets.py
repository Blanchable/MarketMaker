"""UI widgets – control panel, status cards, and main window layout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app_gui.config_store import (
    config_path,
    export_config,
    import_config,
    load_config,
    logs_dir,
    save_config,
)
from app_gui.log_tail import LogViewer
from app_gui.process_runner import ProcessRunner


# ── Style constants ──────────────────────────────────────────────────────────
_STYLE = """
QMainWindow { background-color: #1e1e2e; }
QGroupBox {
    font-weight: bold; font-size: 12px;
    border: 1px solid #45475a; border-radius: 6px;
    margin-top: 10px; padding: 14px 6px 6px 6px;
    color: #cdd6f4;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QLabel { color: #cdd6f4; font-size: 12px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #313244; color: #cdd6f4;
    border: 1px solid #45475a; border-radius: 4px;
    padding: 3px 6px; min-height: 22px; font-size: 12px;
}
QComboBox::drop-down { border: none; }
QCheckBox { color: #cdd6f4; spacing: 5px; font-size: 12px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QPushButton {
    background-color: #45475a; color: #cdd6f4;
    border: 1px solid #585b70; border-radius: 5px;
    padding: 5px 12px; font-weight: bold; min-height: 26px; font-size: 12px;
}
QPushButton:hover { background-color: #585b70; }
QPushButton:pressed { background-color: #6c7086; }
QPushButton:disabled { background-color: #313244; color: #6c7086; }
QPushButton#startBtn { background-color: #a6e3a1; color: #1e1e2e; }
QPushButton#startBtn:hover { background-color: #94e2d5; }
QPushButton#stopBtn { background-color: #f38ba8; color: #1e1e2e; }
QPushButton#stopBtn:hover { background-color: #eba0ac; }
QPushButton#cancelBtn { background-color: #fab387; color: #1e1e2e; }
QPushButton#closeAllBtn { background-color: #f38ba8; color: #1e1e2e; }
QPushButton#closeAllBtn:hover { background-color: #eba0ac; }
QSplitter::handle { background-color: #45475a; width: 2px; }
QScrollArea { border: none; background: transparent; }
"""


# ── Status Card ──────────────────────────────────────────────────────────────
class StatusCard(QFrame):
    def __init__(self, label: str, initial: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "StatusCard { background-color: #313244; border: 1px solid #45475a;"
            " border-radius: 6px; padding: 6px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(1)
        self._label = QLabel(label)
        self._label.setStyleSheet("color: #a6adc8; font-size: 10px;")
        self._value = QLabel(initial)
        self._value.setStyleSheet("color: #cdd6f4; font-size: 14px; font-weight: bold;")
        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, text: str, color: str | None = None) -> None:
        style = f"color: {color};" if color else "color: #cdd6f4;"
        self._value.setStyleSheet(f"{style} font-size: 14px; font-weight: bold;")
        self._value.setText(text)


# ── Control Panel (left side) ────────────────────────────────────────────────
class ControlPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ── Row 1: Environment + Strategy side by side ──
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # Environment column
        env_group = QGroupBox("Environment")
        env_grid = QGridLayout(env_group)
        env_grid.setSpacing(4)
        env_grid.addWidget(QLabel("Env:"), 0, 0)
        self.env_combo = QComboBox()
        self.env_combo.addItems(["demo", "prod"])
        env_grid.addWidget(self.env_combo, 0, 1)
        env_grid.addWidget(QLabel("Mode:"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Paper", "Live"])
        env_grid.addWidget(self.mode_combo, 1, 1)
        env_grid.addWidget(QLabel("Market:"), 2, 0)
        self.market_mode_combo = QComboBox()
        self.market_mode_combo.addItems(["Sports (CBB)", "Crypto (BTC)"])
        env_grid.addWidget(self.market_mode_combo, 2, 1)
        top_row.addWidget(env_group)

        # Strategy column
        strat_group = QGroupBox("Strategy")
        strat_vbox = QVBoxLayout(strat_group)
        strat_vbox.setSpacing(3)
        self.mm_check = QCheckBox("Market Making")
        self.mm_check.setChecked(True)
        self.sniper_check = QCheckBox("Sniper")
        self.sniper_check.setChecked(True)
        strat_vbox.addWidget(self.mm_check)
        strat_vbox.addWidget(self.sniper_check)

        top_row.addWidget(strat_group)
        layout.addLayout(top_row)

        # ── Row 2: Risk & Exit Settings ──
        risk_group = QGroupBox("Risk Limits & Auto-Exit")
        risk_grid = QGridLayout(risk_group)
        risk_grid.setSpacing(4)
        self.daily_stop = self._spin(risk_grid, 0, 0, "Daily Stop $", 200, 0, 10000)
        self.max_gross = self._spin(risk_grid, 0, 2, "Gross Exp $", 250, 0, 50000)
        self.max_net = self._spin(risk_grid, 1, 0, "Net Exp $", 150, 0, 50000)
        self.max_per_mkt = self._spin(risk_grid, 1, 2, "Per Mkt $", 125, 0, 10000)
        self.max_order = self._int_spin(risk_grid, 2, 0, "Order Size", 25, 1, 500)
        self.max_markets = self._int_spin(risk_grid, 2, 2, "Max Mkts", 50, 1, 5000)

        # Take-profit / stop-loss in cents
        self.take_profit_spin = self._int_spin(risk_grid, 3, 0, "TP cents", 5, 0, 99)
        self.take_profit_spin.setToolTip("Auto-sell when mid is this many cents above entry (0 = off)")
        self.stop_loss_spin = self._int_spin(risk_grid, 3, 2, "SL cents", 5, 0, 99)
        self.stop_loss_spin.setToolTip("Auto-sell when mid is this many cents below entry (0 = off)")

        layout.addWidget(risk_group)

        # ── Row 3: Credentials (single row) ──
        cred_group = QGroupBox("Credentials")
        cred_grid = QGridLayout(cred_group)
        cred_grid.setSpacing(4)
        cred_grid.addWidget(QLabel("Key ID:"), 0, 0)
        self.key_id_input = QLineEdit()
        self.key_id_input.setPlaceholderText("your-kalshi-key-id")
        cred_grid.addWidget(self.key_id_input, 0, 1, 1, 3)
        cred_grid.addWidget(QLabel("Key File:"), 1, 0)
        self.key_path_input = QLineEdit()
        self.key_path_input.setPlaceholderText("path/to/kalshi.key")
        cred_grid.addWidget(self.key_path_input, 1, 1, 1, 2)
        self.key_browse_btn = QPushButton("...")
        self.key_browse_btn.setFixedWidth(30)
        self.key_browse_btn.clicked.connect(self._browse_key)
        cred_grid.addWidget(self.key_browse_btn, 1, 3)
        layout.addWidget(cred_group)

        # ── Row 4: Action buttons (2-column grid) ──
        btn_group = QGroupBox("Actions")
        btn_grid = QGridLayout(btn_group)
        btn_grid.setSpacing(4)

        self.start_btn = QPushButton("Start Bot")
        self.start_btn.setObjectName("startBtn")
        self.stop_btn = QPushButton("Stop Bot")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Cancel All Orders")
        self.cancel_btn.setObjectName("cancelBtn")
        self.close_all_btn = QPushButton("Sell All + Cancel")
        self.close_all_btn.setObjectName("closeAllBtn")
        self.logs_btn = QPushButton("Open Logs")
        self.export_btn = QPushButton("Export Config")
        self.import_btn = QPushButton("Import Config")

        btn_grid.addWidget(self.start_btn, 0, 0)
        btn_grid.addWidget(self.stop_btn, 0, 1)
        btn_grid.addWidget(self.cancel_btn, 1, 0)
        btn_grid.addWidget(self.close_all_btn, 1, 1)
        btn_grid.addWidget(self.logs_btn, 2, 0)
        btn_grid.addWidget(self.export_btn, 2, 1)
        btn_grid.addWidget(self.import_btn, 3, 0, 1, 2)

        layout.addWidget(btn_group)
        layout.addStretch()

    def _spin(self, grid: QGridLayout, row: int, col: int, label: str,
              default: float, lo: float, hi: float) -> QDoubleSpinBox:
        grid.addWidget(QLabel(label), row, col)
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setValue(default)
        spin.setSingleStep(10)
        grid.addWidget(spin, row, col + 1)
        return spin

    def _int_spin(self, grid: QGridLayout, row: int, col: int, label: str,
                  default: int, lo: int, hi: int) -> QSpinBox:
        grid.addWidget(QLabel(label), row, col)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        grid.addWidget(spin, row, col + 1)
        return spin

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Private Key File", "", "Key Files (*.key *.pem);;All Files (*)"
        )
        if path:
            self.key_path_input.setText(path)

    def get_config(self) -> dict[str, Any]:
        paper = self.mode_combo.currentText() == "Paper"
        live = self.mode_combo.currentText() == "Live"
        market_mode = "sports" if self.market_mode_combo.currentIndex() == 0 else "crypto"
        return {
            "environment": self.env_combo.currentText(),
            "paper_mode": paper,
            "live_trading": live,
            "market_mode": market_mode,
            "key_id": self.key_id_input.text().strip(),
            "private_key_path": self.key_path_input.text().strip(),
            "mm_enabled": self.mm_check.isChecked(),
            "sniper_enabled": self.sniper_check.isChecked(),
            "mm_take_profit_cents": self.take_profit_spin.value(),
            "mm_stop_loss_cents": self.stop_loss_spin.value(),
            "daily_stop_dollars": self.daily_stop.value(),
            "max_gross_exposure_dollars": self.max_gross.value(),
            "max_net_exposure_dollars": self.max_net.value(),
            "max_exposure_per_market_dollars": self.max_per_mkt.value(),
            "max_order_size_contracts": self.max_order.value(),
            "max_markets": self.max_markets.value(),
        }

    def set_config(self, cfg: dict[str, Any]) -> None:
        idx = self.env_combo.findText(cfg.get("environment", "demo"))
        if idx >= 0:
            self.env_combo.setCurrentIndex(idx)
        self.mode_combo.setCurrentIndex(1 if cfg.get("live_trading") else 0)
        self.market_mode_combo.setCurrentIndex(0 if cfg.get("market_mode", "sports") == "sports" else 1)
        self.mm_check.setChecked(cfg.get("mm_enabled", True))
        self.sniper_check.setChecked(cfg.get("sniper_enabled", True))
        self.take_profit_spin.setValue(int(cfg.get("mm_take_profit_cents", 5)))
        self.stop_loss_spin.setValue(int(cfg.get("mm_stop_loss_cents", 5)))
        self.daily_stop.setValue(cfg.get("daily_stop_dollars", 200))
        self.max_gross.setValue(cfg.get("max_gross_exposure_dollars", 250))
        self.max_net.setValue(cfg.get("max_net_exposure_dollars", 150))
        self.max_per_mkt.setValue(cfg.get("max_exposure_per_market_dollars", 125))
        self.max_order.setValue(int(cfg.get("max_order_size_contracts", 25)))
        self.max_markets.setValue(int(cfg.get("max_markets", 50)))
        self.key_id_input.setText(cfg.get("key_id", ""))
        self.key_path_input.setText(cfg.get("private_key_path", ""))


# ── Status Panel ─────────────────────────────────────────────────────────────
class StatusPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            "QLabel { background-color: #f38ba8; color: #1e1e2e; font-weight: bold;"
            " padding: 5px 8px; border-radius: 4px; font-size: 11px; }"
        )
        self._banner.setVisible(False)
        outer.addWidget(self._banner)

        grid = QGridLayout()
        grid.setSpacing(6)

        self.connection = StatusCard("Connection", "Disconnected")
        self.mode_card = StatusCard("Market Mode", "—")
        self.tradeable_card = StatusCard("Tradeable", "—")
        self.subscribed_card = StatusCard("Subscribed", "—")
        self.pnl_realized = StatusCard("Realized PnL", "—")
        self.pnl_unrealized = StatusCard("Unrealized PnL", "—")
        self.gross_exp = StatusCard("Gross Exposure", "—")
        self.net_exp = StatusCard("Net Exposure", "—")
        self.kill_switch = StatusCard("Kill Switch", "INACTIVE")

        grid.addWidget(self.connection, 0, 0)
        grid.addWidget(self.mode_card, 0, 1)
        grid.addWidget(self.tradeable_card, 0, 2)
        grid.addWidget(self.subscribed_card, 0, 3)
        grid.addWidget(self.kill_switch, 0, 4)
        grid.addWidget(self.pnl_realized, 1, 0)
        grid.addWidget(self.pnl_unrealized, 1, 1)
        grid.addWidget(self.gross_exp, 1, 2)
        grid.addWidget(self.net_exp, 1, 3)

        outer.addLayout(grid)

    @Slot(dict)
    def update_from_status(self, data: dict[str, Any]) -> None:
        connected = data.get("connected", False)
        self.connection.set_value(
            "Connected" if connected else "Disconnected",
            "#a6e3a1" if connected else "#f38ba8",
        )
        mm = data.get("market_mode", "sports")
        self.mode_card.set_value("CBB Sports" if mm == "sports" else "BTC Crypto", "#89b4fa")

        pnl_r = data.get("pnl_realized_today", 0)
        self.pnl_realized.set_value(f"${pnl_r:+.2f}", "#a6e3a1" if pnl_r >= 0 else "#f38ba8")
        pnl_u = data.get("pnl_unrealized", 0)
        self.pnl_unrealized.set_value(f"${pnl_u:+.2f}", "#a6e3a1" if pnl_u >= 0 else "#f38ba8")

        self.gross_exp.set_value(f"${data.get('gross_exposure', 0):.2f}")
        self.net_exp.set_value(f"${data.get('net_exposure', 0):.2f}")

        kill = data.get("kill_switch", False)
        self.kill_switch.set_value(
            "TRIGGERED" if kill else "INACTIVE",
            "#f38ba8" if kill else "#a6e3a1",
        )

        t_count = data.get("tradeable_tickers", 0)
        s_count = data.get("subscribed_tickers", 0)
        self.tradeable_card.set_value(str(t_count), "#a6e3a1" if t_count > 0 else "#f38ba8")
        self.subscribed_card.set_value(str(s_count), "#a6e3a1" if s_count > 0 else "#fab387")

        if t_count == 0:
            self._banner.setText("No tradeable markets after filters. Open Logs for breakdown.")
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)


# ── Main Window ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Kalshi Bot")
        self.setMinimumSize(1200, 750)
        self.setStyleSheet(_STYLE)

        self._runner = ProcessRunner(self)
        self._build_ui()
        self._connect_signals()
        self._load_persisted_config()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        header = QLabel("Kalshi Bot")
        header.setStyleSheet("color: #cdd6f4; font-size: 20px; font-weight: bold; padding: 2px 0;")
        header.setAlignment(Qt.AlignCenter)
        root.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)

        # Left: controls in a scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._controls = ControlPanel()
        scroll.setWidget(self._controls)
        splitter.addWidget(scroll)

        # Right: status + logs
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self._status = StatusPanel()
        right_layout.addWidget(self._status)
        log_label = QLabel("Log Output")
        log_label.setStyleSheet("color: #a6adc8; font-size: 11px; font-weight: bold;")
        right_layout.addWidget(log_label)
        self._log = LogViewer()
        right_layout.addWidget(self._log, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([400, 800])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    def _connect_signals(self) -> None:
        c = self._controls
        c.start_btn.clicked.connect(self._on_start)
        c.stop_btn.clicked.connect(self._on_stop)
        c.cancel_btn.clicked.connect(self._on_cancel_all)
        c.close_all_btn.clicked.connect(self._on_close_all)
        c.logs_btn.clicked.connect(self._on_open_logs)
        c.export_btn.clicked.connect(self._on_export_config)
        c.import_btn.clicked.connect(self._on_import_config)
        self._runner.line_received.connect(self._log.append_line)
        self._runner.status_received.connect(self._status.update_from_status)
        self._runner.process_started.connect(self._on_process_started)
        self._runner.process_stopped.connect(self._on_process_stopped)

    def _load_persisted_config(self) -> None:
        self._controls.set_config(load_config())

    def _on_start(self) -> None:
        cfg = self._controls.get_config()
        if not cfg.get("key_id"):
            QMessageBox.warning(self, "Validation", "Key ID is required.")
            return
        key_path = cfg.get("private_key_path", "")
        if not key_path or not Path(key_path).exists():
            QMessageBox.warning(self, "Validation", f"Private key file not found:\n{key_path}")
            return
        if cfg.get("environment") == "prod" and cfg.get("live_trading"):
            if not self._confirm_live():
                return
        save_config(cfg)
        self._log.append_line("Starting bot...")
        self._runner.start_bot(cfg)

    def _confirm_live(self) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Live Trading")
        dialog.setStyleSheet(
            "QDialog { background-color: #1e1e2e; }"
            "QLabel { color: #cdd6f4; font-size: 13px; }"
            "QLineEdit { background-color: #313244; color: #cdd6f4;"
            " border: 1px solid #f38ba8; border-radius: 4px; padding: 5px; font-size: 13px; }"
        )
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "You are about to start LIVE trading on PRODUCTION.\n"
            "This will use REAL money.\n\nType LIVE below to confirm:"
        ))
        entry = QLineEdit()
        entry.setPlaceholderText("Type LIVE to confirm")
        layout.addWidget(entry)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return False
        return entry.text().strip() == "LIVE"

    def _on_stop(self) -> None:
        self._log.append_line("Stopping bot...")
        self._runner.stop_bot()
        cfg = self._controls.get_config()
        if cfg.get("live_trading") and not cfg.get("paper_mode", True):
            self._log.append_line("Sending cancel-all after stop...")
            self._runner.cancel_all_orders(cfg)

    def _on_cancel_all(self) -> None:
        cfg = self._controls.get_config()
        if not cfg.get("key_id"):
            QMessageBox.warning(self, "Validation", "Key ID required for cancel-all.")
            return
        self._log.append_line("Cancelling all orders...")
        self._runner.cancel_all_orders(cfg)

    def _on_close_all(self) -> None:
        cfg = self._controls.get_config()
        if not cfg.get("key_id"):
            QMessageBox.warning(self, "Validation", "Key ID required.")
            return
        confirm = QMessageBox.question(
            self, "Confirm Sell All",
            "This will cancel ALL open orders and sell ALL open positions at market price.\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._log.append_line("Selling all positions and cancelling all orders...")
        self._runner.close_all_positions(cfg)

    def _on_open_logs(self) -> None:
        ld = logs_dir()
        if sys.platform == "win32":
            os.startfile(str(ld))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ld)])
        else:
            subprocess.Popen(["xdg-open", str(ld)])

    def _on_export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Config", "kalshi_bot_config.json", "JSON (*.json)")
        if path:
            export_config(self._controls.get_config(), Path(path))
            self._log.append_line(f"Config exported to {path}")

    def _on_import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Config", "", "JSON (*.json);;All (*)")
        if path:
            self._controls.set_config(import_config(Path(path)))
            self._log.append_line(f"Config imported from {path}")

    @Slot()
    def _on_process_started(self) -> None:
        self._controls.start_btn.setEnabled(False)
        self._controls.stop_btn.setEnabled(True)
        self._status.connection.set_value("Starting...", "#f9e2af")

    @Slot(int)
    def _on_process_stopped(self, exit_code: int) -> None:
        self._controls.start_btn.setEnabled(True)
        self._controls.stop_btn.setEnabled(False)
        self._status.connection.set_value("Disconnected", "#f38ba8")
        self._log.append_line(f"Bot process exited (code {exit_code})")

    def closeEvent(self, event: Any) -> None:
        if self._runner.is_running:
            self._runner.stop_bot()
        save_config(self._controls.get_config())
        super().closeEvent(event)
