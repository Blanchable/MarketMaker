"""Subprocess manager – launches the bot CLI and captures output."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from app_gui.config_store import config_path, save_config


class ProcessRunner(QObject):
    """Launches the bot as a subprocess and streams its output."""

    line_received = Signal(str)
    status_received = Signal(dict)
    process_started = Signal()
    process_stopped = Signal(int)  # exit code

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start_bot(self, cfg: dict[str, Any]) -> None:
        """Save config and launch the bot subprocess."""
        if self.is_running:
            return

        save_config(cfg)
        self._stop_event.clear()

        env_name = cfg.get("environment", "demo")
        paper = cfg.get("paper_mode", True)
        live = cfg.get("live_trading", False)

        cmd = [
            sys.executable, "-m", "bot.cli", "run",
            "--env", env_name,
            "--paper" if paper else "--no-paper",
            "--live" if live else "--no-live",
            "--config", str(config_path()),
        ]

        env = os.environ.copy()
        if cfg.get("key_id"):
            env["KALSHI_KEY_ID"] = cfg["key_id"]
        if cfg.get("private_key_path"):
            env["KALSHI_PRIVATE_KEY_PATH"] = str(cfg["private_key_path"])
        if cfg.get("market_mode"):
            env["MARKET_MODE"] = cfg["market_mode"]

        # Determine the src/ directory to use as cwd for module resolution
        src_dir = Path(__file__).resolve().parent.parent
        env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                creationflags=creation_flags,
            )
        except Exception as exc:
            self.line_received.emit(f"ERROR: Failed to start bot: {exc}")
            return

        self.process_started.emit()
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def stop_bot(self) -> None:
        """Gracefully stop the bot subprocess."""
        if not self.is_running or self._proc is None:
            return

        self._stop_event.set()

        if sys.platform == "win32":
            try:
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                self._proc.terminate()
        else:
            self._proc.terminate()

        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=3)

        exit_code = self._proc.returncode or 0
        self._proc = None
        self.process_stopped.emit(exit_code)

    def cancel_all_orders(self, cfg: dict[str, Any]) -> None:
        """Run the cancel-all command as a one-shot subprocess."""
        save_config(cfg)

        cmd = [
            sys.executable, "-m", "bot.cli", "cancel-all",
            "--env", cfg.get("environment", "demo"),
            "--config", str(config_path()),
        ]

        env = os.environ.copy()
        if cfg.get("key_id"):
            env["KALSHI_KEY_ID"] = cfg["key_id"]
        if cfg.get("private_key_path"):
            env["KALSHI_PRIVATE_KEY_PATH"] = str(cfg["private_key_path"])

        src_dir = Path(__file__).resolve().parent.parent
        env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.line_received.emit(result.stdout or "cancel-all completed")
            if result.stderr:
                self.line_received.emit(result.stderr)
        except subprocess.TimeoutExpired:
            self.line_received.emit("ERROR: cancel-all timed out")
        except Exception as exc:
            self.line_received.emit(f"ERROR: cancel-all failed: {exc}")

    def close_all_positions(self, cfg: dict[str, Any]) -> None:
        """Run the close-all command: cancel orders + sell all positions."""
        save_config(cfg)

        cmd = [
            sys.executable, "-m", "bot.cli", "close-all",
            "--env", cfg.get("environment", "demo"),
            "--config", str(config_path()),
        ]

        env = os.environ.copy()
        if cfg.get("key_id"):
            env["KALSHI_KEY_ID"] = cfg["key_id"]
        if cfg.get("private_key_path"):
            env["KALSHI_PRIVATE_KEY_PATH"] = str(cfg["private_key_path"])
        if cfg.get("market_mode"):
            env["MARKET_MODE"] = cfg["market_mode"]

        src_dir = Path(__file__).resolve().parent.parent
        env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.line_received.emit(result.stdout or "close-all completed")
            if result.stderr:
                self.line_received.emit(result.stderr)
        except subprocess.TimeoutExpired:
            self.line_received.emit("ERROR: close-all timed out")
        except Exception as exc:
            self.line_received.emit(f"ERROR: close-all failed: {exc}")

    def _read_output(self) -> None:
        """Background thread: read subprocess stdout line by line."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        import json as _json

        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n\r")
                if not stripped:
                    continue

                if stripped.startswith("STATUS_JSON: "):
                    try:
                        payload = _json.loads(stripped[len("STATUS_JSON: "):])
                        self.status_received.emit(payload)
                    except _json.JSONDecodeError:
                        pass
                else:
                    self.line_received.emit(stripped)

                if self._stop_event.is_set():
                    break
        except Exception:
            pass

        exit_code = 0
        if proc:
            try:
                proc.wait(timeout=2)
                exit_code = proc.returncode or 0
            except Exception:
                pass
        self.process_stopped.emit(exit_code)
