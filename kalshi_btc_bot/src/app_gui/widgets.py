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
QMainWindow {
    background-color: #1e1e2e;
}
QGroupBox {
    font-weight: bold;
    border: 1px solid #45475a;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 16px;
    color: #cdd6f4;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QLabel {
    color: #cdd6f4;
    font-size: 13px;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 24px;
}
QComboBox::drop-down {
    border: none;
}
QCheckBox {
    color: #cdd6f4;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
}
QPushButton {
    background-color: #45475a;
    color: #cdd6f4;
    border: 1px solid #585b70;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: bold;
    min-height: 28px;
}
QPushButton:hover {
    background-color: #585b70;
}
QPushButton:pressed {
    background-color: #6c7086;
}
QPushButton:disabled {
    background-color: #313244;
    color: #6c7086;
}
QPushButton#startBtn {
    background-color: #a6e3a1;
    color: #1e1e2e;
}
QPushButton#startBtn:hover {
    background-color: #94e2d5;
}
QPushButton#stopBtn {
    background-color: #f38ba8;
    color: #1e1e2e;
}
QPushButton#stopBtn:hover {
    background-color: #eba0ac;
}
QPushButton#cancelBtn {
    background-color: #fab387;
    color: #1e1e2e;
}
QSplitter::handle {
    background-color: #45475a;
    width: 2px;
}
"""


# ── Status Card ──────────────────────────────────────────────────────────────
class StatusCard(QFrame):
    """Single metric display card."""

    def __init__(self, label: str, initial: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "StatusCard {"
            "  background-color: #313244;"
            "  border: 1px solid #45475a;"
            "  border-radius: 6px;"
            "  padding: 8px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self._label = QLabel(label)
        self._label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self._value = QLabel(initial)
        self._value.setStyleSheet("color: #cdd6f4; font-size: 16px; font-weight: bold;")

        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, text: str, color: str | None = None) -> None:
        style = f"color: {color};" if color else "color: #cdd6f4;"
        self._value.setStyleSheet(f"{style} font-size: 16px; font-weight: bold;")
        self._value.setText(text)


# ── Control Panel (left side) ────────────────────────────────────────────────
class ControlPanel(QWidget):
    """Left-side panel with environment, mode, strategy, risk, credentials, and buttons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Environment & Mode ──
        env_group = QGroupBox("Environment")
        env_layout = QGridLayout(env_group)

        env_layout.addWidget(QLabel("Environment:"), 0, 0)
        self.env_combo = QComboBox()
        self.env_combo.addItems(["demo", "prod"])
        env_layout.addWidget(self.env_combo, 0, 1)

        env_layout.addWidget(QLabel("Trading Mode:"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Paper", "Live"])
        env_layout.addWidget(self.mode_combo, 1, 1)

        env_layout.addWidget(QLabel("Market:"), 2, 0)
        self.market_mode_combo = QComboBox()
        self.market_mode_combo.addItems(["Sports (College Basketball)", "Crypto (BTC)"])
        env_layout.addWidget(self.market_mode_combo, 2, 1)

        layout.addWidget(env_group)

        # ── Strategy Toggles ──
        strat_group = QGroupBox("Strategy")
        strat_layout = QVBoxLayout(strat_group)
        self.mm_check = QCheckBox("Enable Market Making")
        self.mm_check.setChecked(True)
        self.sniper_check = QCheckBox("Enable Sniper")
        self.sniper_check.setChecked(True)
        strat_layout.addWidget(self.mm_check)
        strat_layout.addWidget(self.sniper_check)

        # TP/SL settings inside strategy group
        tpsl_grid = QGridLayout()
        tpsl_grid.addWidget(QLabel("Take Profit (%):"), 0, 0)
        self.take_profit_spin = QDoubleSpinBox()
        self.take_profit_spin.setRange(0, 500)
        self.take_profit_spin.setDecimals(1)
        self.take_profit_spin.setValue(15.0)
        self.take_profit_spin.setSingleStep(1)
        self.take_profit_spin.setToolTip("Sell when position is this % above entry (0 = disabled)")
        tpsl_grid.addWidget(self.take_profit_spin, 0, 1)

        tpsl_grid.addWidget(QLabel("Stop Loss (%):"), 1, 0)
        self.stop_loss_spin = QDoubleSpinBox()
        self.stop_loss_spin.setRange(0, 500)
        self.stop_loss_spin.setDecimals(1)
        self.stop_loss_spin.setValue(15.0)
        self.stop_loss_spin.setSingleStep(1)
        self.stop_loss_spin.setToolTip("Sell when position is this % below entry (0 = disabled)")
        tpsl_grid.addWidget(self.stop_loss_spin, 1, 1)

        strat_layout.addLayout(tpsl_grid)
        layout.addWidget(strat_group)

        # ── Risk Settings ──
        risk_group = QGroupBox("Risk Settings")
        risk_layout = QGridLayout(risk_group)

        self.daily_stop = self._add_double_spin(risk_layout, 0, "Daily Stop ($):", 200, 0, 10000)
        self.max_gross = self._add_double_spin(risk_layout, 1, "Max Gross Exp ($):", 250, 0, 50000)
        self.max_net = self._add_double_spin(risk_layout, 2, "Max Net Exp ($):", 150, 0, 50000)
        self.max_per_mkt = self._add_double_spin(risk_layout, 3, "Max Per Market ($):", 125, 0, 10000)
        self.max_order = self._add_int_spin(risk_layout, 4, "Max Order Size:", 25, 1, 500)

        layout.addWidget(risk_group)

        # ── Credentials ──
        cred_group = QGroupBox("Credentials")
        cred_layout = QGridLayout(cred_group)

        cred_layout.addWidget(QLabel("Key ID:"), 0, 0)
        self.key_id_input = QLineEdit()
        self.key_id_input.setPlaceholderText("your-kalshi-key-id")
        cred_layout.addWidget(self.key_id_input, 0, 1)

        cred_layout.addWidget(QLabel("Private Key:"), 1, 0)
        key_row = QHBoxLayout()
        self.key_path_input = QLineEdit()
        self.key_path_input.setPlaceholderText("path/to/kalshi.key")
        self.key_browse_btn = QPushButton("...")
        self.key_browse_btn.setFixedWidth(30)
        self.key_browse_btn.clicked.connect(self._browse_key)
        key_row.addWidget(self.key_path_input)
        key_row.addWidget(self.key_browse_btn)
        cred_layout.addLayout(key_row, 1, 1)

        layout.addWidget(cred_group)

        # ── Action Buttons ──
        btn_group = QGroupBox("Actions")
        btn_layout = QVBoxLayout(btn_group)

        self.start_btn = QPushButton("Start Bot")
        self.start_btn.setObjectName("startBtn")
        self.stop_btn = QPushButton("Stop Bot")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Cancel All Orders")
        self.cancel_btn.setObjectName("cancelBtn")
        self.close_all_btn = QPushButton("Sell All + Cancel Orders")
        self.close_all_btn.setObjectName("cancelBtn")
        self.logs_btn = QPushButton("Open Logs Folder")

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.close_all_btn)
        btn_layout.addWidget(self.logs_btn)

        # Config import/export
        cfg_row = QHBoxLayout()
        self.export_btn = QPushButton("Export Config")
        self.import_btn = QPushButton("Import Config")
        cfg_row.addWidget(self.export_btn)
        cfg_row.addWidget(self.import_btn)
        btn_layout.addLayout(cfg_row)

        layout.addWidget(btn_group)
        layout.addStretch()

    def _add_double_spin(
        self, grid: QGridLayout, row: int, label: str, default: float, lo: float, hi: float
    ) -> QDoubleSpinBox:
        grid.addWidget(QLabel(label), row, 0)
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setValue(default)
        spin.setSingleStep(10)
        grid.addWidget(spin, row, 1)
        return spin

    def _add_int_spin(
        self, grid: QGridLayout, row: int, label: str, default: int, lo: int, hi: int
    ) -> QSpinBox:
        grid.addWidget(QLabel(label), row, 0)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        grid.addWidget(spin, row, 1)
        return spin

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Private Key File", "", "Key Files (*.key *.pem);;All Files (*)"
        )
        if path:
            self.key_path_input.setText(path)

    def get_config(self) -> dict[str, Any]:
        """Collect all widget values into a config dict."""
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
            "mm_take_profit_pct": self.take_profit_spin.value(),
            "mm_stop_loss_pct": self.stop_loss_spin.value(),
            "daily_stop_dollars": self.daily_stop.value(),
            "max_gross_exposure_dollars": self.max_gross.value(),
            "max_net_exposure_dollars": self.max_net.value(),
            "max_exposure_per_market_dollars": self.max_per_mkt.value(),
            "max_order_size_contracts": self.max_order.value(),
        }

    def set_config(self, cfg: dict[str, Any]) -> None:
        """Populate widgets from a config dict."""
        idx = self.env_combo.findText(cfg.get("environment", "demo"))
        if idx >= 0:
            self.env_combo.setCurrentIndex(idx)

        if cfg.get("live_trading"):
            self.mode_combo.setCurrentIndex(1)
        else:
            self.mode_combo.setCurrentIndex(0)

        market_mode = cfg.get("market_mode", "sports")
        self.market_mode_combo.setCurrentIndex(0 if market_mode == "sports" else 1)

        self.mm_check.setChecked(cfg.get("mm_enabled", True))
        self.sniper_check.setChecked(cfg.get("sniper_enabled", True))
        self.take_profit_spin.setValue(cfg.get("mm_take_profit_pct", 15.0))
        self.stop_loss_spin.setValue(cfg.get("mm_stop_loss_pct", 15.0))
        self.daily_stop.setValue(cfg.get("daily_stop_dollars", 200))
        self.max_gross.setValue(cfg.get("max_gross_exposure_dollars", 250))
        self.max_net.setValue(cfg.get("max_net_exposure_dollars", 150))
        self.max_per_mkt.setValue(cfg.get("max_exposure_per_market_dollars", 125))
        self.max_order.setValue(int(cfg.get("max_order_size_contracts", 25)))
        self.key_id_input.setText(cfg.get("key_id", ""))
        self.key_path_input.setText(cfg.get("private_key_path", ""))


# ── Status Panel ─────────────────────────────────────────────────────────────
class StatusPanel(QWidget):
    """Grid of status cards + zero-markets warning banner."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # Warning banner (hidden by default)
        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            "QLabel { background-color: #f38ba8; color: #1e1e2e; font-weight: bold;"
            "  padding: 6px 10px; border-radius: 4px; font-size: 12px; }"
        )
        self._banner.setVisible(False)
        outer.addWidget(self._banner)

        grid = QGridLayout()
        grid.setSpacing(8)

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
        grid.addWidget(self.pnl_realized, 1, 0)
        grid.addWidget(self.pnl_unrealized, 1, 1)
        grid.addWidget(self.gross_exp, 1, 2)
        grid.addWidget(self.net_exp, 1, 3)
        grid.addWidget(self.kill_switch, 2, 0)

        outer.addLayout(grid)

    @Slot(dict)
    def update_from_status(self, data: dict[str, Any]) -> None:
        """Update all cards from a STATUS_JSON payload."""
        connected = data.get("connected", False)
        self.connection.set_value(
            "Connected" if connected else "Disconnected",
            "#a6e3a1" if connected else "#f38ba8",
        )

        # Market mode
        mm = data.get("market_mode", "sports")
        mode_label = "CBB Sports" if mm == "sports" else "BTC Crypto"
        self.mode_card.set_value(mode_label, "#89b4fa")

        pnl_r = data.get("pnl_realized_today", 0)
        pnl_color = "#a6e3a1" if pnl_r >= 0 else "#f38ba8"
        self.pnl_realized.set_value(f"${pnl_r:+.2f}", pnl_color)

        pnl_u = data.get("pnl_unrealized", 0)
        pnl_u_color = "#a6e3a1" if pnl_u >= 0 else "#f38ba8"
        self.pnl_unrealized.set_value(f"${pnl_u:+.2f}", pnl_u_color)

        gross = data.get("gross_exposure", 0)
        self.gross_exp.set_value(f"${gross:.2f}")

        net = data.get("net_exposure", 0)
        self.net_exp.set_value(f"${net:.2f}")

        kill = data.get("kill_switch", False)
        self.kill_switch.set_value(
            "TRIGGERED" if kill else "INACTIVE",
            "#f38ba8" if kill else "#a6e3a1",
        )

        # Tradeable / subscribed tickers
        t_count = data.get("tradeable_tickers", 0)
        s_count = data.get("subscribed_tickers", 0)
        t_color = "#a6e3a1" if t_count > 0 else "#f38ba8"
        s_color = "#a6e3a1" if s_count > 0 else "#fab387"
        self.tradeable_card.set_value(str(t_count), t_color)
        self.subscribed_card.set_value(str(s_count), s_color)

        # Banner
        if t_count == 0:
            self._banner.setText(
                "No tradeable markets after filters. Open Logs for filter breakdown."
            )
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)


# ── Main Window ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    """Primary application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Kalshi Bot")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(_STYLE)

        self._runner = ProcessRunner(self)
        self._build_ui()
        self._connect_signals()
        self._load_persisted_config()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Header
        header = QLabel("Kalshi Bot")
        header.setStyleSheet(
            "color: #cdd6f4; font-size: 22px; font-weight: bold; padding: 4px 0;"
        )
        header.setAlignment(Qt.AlignCenter)
        root.addWidget(header)

        # Splitter: left controls | right status+logs
        splitter = QSplitter(Qt.Horizontal)

        # Left: controls in a scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._controls = ControlPanel()
        scroll.setWidget(self._controls)
        splitter.addWidget(scroll)

        # Right: status + logs
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._status = StatusPanel()
        right_layout.addWidget(self._status)

        log_label = QLabel("Log Output")
        log_label.setStyleSheet("color: #a6adc8; font-size: 12px; font-weight: bold;")
        right_layout.addWidget(log_label)

        self._log = LogViewer()
        right_layout.addWidget(self._log, stretch=1)

        splitter.addWidget(right)
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
        cfg = load_config()
        self._controls.set_config(cfg)

    def _on_start(self) -> None:
        cfg = self._controls.get_config()

        # Validate credentials
        if not cfg.get("key_id"):
            QMessageBox.warning(self, "Validation", "Key ID is required.")
            return
        key_path = cfg.get("private_key_path", "")
        if not key_path or not Path(key_path).exists():
            QMessageBox.warning(
                self, "Validation", f"Private key file not found:\n{key_path}"
            )
            return

        # Live trading safety confirmation
        if cfg.get("environment") == "prod" and cfg.get("live_trading"):
            ok = self._confirm_live()
            if not ok:
                return

        save_config(cfg)
        self._log.append_line("Starting bot...")
        self._runner.start_bot(cfg)

    def _confirm_live(self) -> bool:
        """Show a modal requiring the user to type 'LIVE' to confirm."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirm Live Trading")
        dialog.setStyleSheet(
            "QDialog { background-color: #1e1e2e; }"
            "QLabel { color: #cdd6f4; font-size: 14px; }"
            "QLineEdit { background-color: #313244; color: #cdd6f4;"
            "  border: 1px solid #f38ba8; border-radius: 4px; padding: 6px; font-size: 14px; }"
        )
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "You are about to start LIVE trading on PRODUCTION.\n"
            "This will use REAL money.\n\n"
            "Type LIVE below to confirm:"
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

        # If live, also cancel all orders
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
            self,
            "Confirm Sell All",
            "This will cancel ALL open orders and sell ALL open positions at market price.\n\n"
            "Are you sure?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Config", "kalshi_bot_config.json", "JSON Files (*.json)"
        )
        if path:
            cfg = self._controls.get_config()
            export_config(cfg, Path(path))
            self._log.append_line(f"Config exported to {path}")

    def _on_import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Config", "", "JSON Files (*.json);;All Files (*)"
        )
        if path:
            cfg = import_config(Path(path))
            self._controls.set_config(cfg)
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
        """Ensure bot is stopped on window close."""
        if self._runner.is_running:
            self._runner.stop_bot()
        cfg = self._controls.get_config()
        save_config(cfg)
        super().closeEvent(event)
