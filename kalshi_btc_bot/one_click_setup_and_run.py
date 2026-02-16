#!/usr/bin/env python3
"""
One-Click Setup & Run for the Kalshi BTC Bot.

This script:
  1. Creates a .venv virtual environment (if missing).
  2. Installs all dependencies (if not yet installed).
  3. Runs a first-time setup wizard to create .env (if missing).
  4. Launches the desktop GUI.

Works on Windows, macOS, and Linux.
Run via RUN_ME_FIRST.bat (Windows) or run_me_first.sh (Mac/Linux).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
GUI_MODULE = "app_gui.main"
GUI_FILE = PROJECT_ROOT / "src" / "app_gui" / "main.py"

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
    VENV_PIP = VENV_DIR / "Scripts" / "pip.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
    VENV_PIP = VENV_DIR / "bin" / "pip"


# ── Helpers ──────────────────────────────────────────────────────────────────

def info(msg: str) -> None:
    print(f"\n  [*] {msg}")


def success(msg: str) -> None:
    print(f"  [OK] {msg}")


def warn(msg: str) -> None:
    print(f"  [!] {msg}")


def error(msg: str) -> None:
    print(f"\n  [ERROR] {msg}")


def run_cmd(cmd: list[str], **kwargs) -> None:
    """Run a command, printing it first. Raises on failure."""
    display = " ".join(str(c) for c in cmd)
    print(f"      $ {display}")
    subprocess.check_call(cmd, **kwargs)


def prompt(label: str, default: str = "", required: bool = False, secret: bool = False) -> str:
    """Prompt the user for input with an optional default."""
    if default:
        display = f"  {label} [{default}]: "
    else:
        display = f"  {label}: "

    while True:
        value = input(display).strip()
        # Strip surrounding quotes (common on Windows paste)
        if value and len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        if not value:
            value = default
        if required and not value:
            print("    (required — please enter a value)")
            continue
        return value


def prompt_choice(label: str, choices: list[str], default: str) -> str:
    """Prompt the user to pick from a list of choices."""
    choices_str = "/".join(choices)
    while True:
        value = prompt(f"{label} ({choices_str})", default=default)
        if value.lower() in [c.lower() for c in choices]:
            return value.lower()
        print(f"    (please enter one of: {choices_str})")


def prompt_bool(label: str, default: bool = False) -> bool:
    """Prompt for a yes/no boolean."""
    default_str = "Y/n" if default else "y/N"
    value = prompt(f"{label} ({default_str})", default="y" if default else "n")
    return value.lower() in ("y", "yes", "true", "1")


# ── Step 1: Virtual environment ─────────────────────────────────────────────

def ensure_venv() -> None:
    """Create .venv if it doesn't exist."""
    if VENV_PYTHON.exists():
        success(f"Virtual environment already exists at {VENV_DIR.name}/")
        return

    info("Creating virtual environment (.venv) ...")
    run_cmd([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not VENV_PYTHON.exists():
        error(f"Failed to create virtual environment. Expected: {VENV_PYTHON}")
        sys.exit(1)
    success("Virtual environment created.")


# ── Step 2: Install dependencies ────────────────────────────────────────────

def install_deps() -> None:
    """Install project dependencies into the venv."""
    # Quick check: if the project is already installed, skip
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import bot; import app_gui"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            success("Dependencies already installed — skipping.")
            return
    except Exception:
        pass

    info("Upgrading pip ...")
    run_cmd([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "--quiet"])

    info("Installing dependencies (this may take a minute) ...")
    if PYPROJECT.exists():
        run_cmd(
            [str(VENV_PYTHON), "-m", "pip", "install", "-e", ".[dev]", "--quiet"],
            cwd=str(PROJECT_ROOT),
        )
    elif REQUIREMENTS.exists():
        run_cmd(
            [str(VENV_PYTHON), "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
            cwd=str(PROJECT_ROOT),
        )
    else:
        error("No pyproject.toml or requirements.txt found. Cannot install dependencies.")
        sys.exit(1)

    success("All dependencies installed.")


# ── Step 3: Setup wizard (.env) ─────────────────────────────────────────────

def read_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file into a dict (ignoring comments)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip inline comments
            if "  #" in value:
                value = value[:value.index("  #")].strip()
            elif value and value[0] not in ('"', "'") and " #" in value:
                value = value[:value.index(" #")].strip()
            result[key] = value
    return result


def write_env_file(values: dict[str, str]) -> None:
    """Write a .env file from a dict, grouped with comments."""
    lines: list[str] = []
    lines.append("# ── Kalshi Credentials (auto-generated by setup wizard) ────────────")
    cred_keys = ["ENVIRONMENT", "KALSHI_KEY_ID", "KALSHI_PRIVATE_KEY_PATH", "LIVE_TRADING"]
    for k in cred_keys:
        if k in values:
            lines.append(f"{k}={values[k]}")

    lines.append("")
    lines.append("# ── Market Selection ─────────────────────────────────────────────────")
    market_keys = [
        "SERIES_CATEGORY", "SERIES_TAGS", "BTC_SPOT_FEED", "BTC_SYMBOL",
        "REFRESH_MARKETS_SECONDS", "STALE_MS", "MARKET_STATUS",
        "MIN_24H_VOLUME", "MAX_SPREAD_CENTS", "MIN_SPREAD_CENTS", "MIN_DEPTH_CONTRACTS",
    ]
    for k in market_keys:
        if k in values:
            lines.append(f"{k}={values[k]}")

    lines.append("")
    lines.append("# ── Risk / Capital ───────────────────────────────────────────────────")
    risk_keys = [
        "START_BANKROLL_DOLLARS", "DAILY_STOP_DOLLARS",
        "MAX_GROSS_EXPOSURE_DOLLARS", "MAX_NET_EXPOSURE_DOLLARS",
        "MAX_EXPOSURE_PER_MARKET_DOLLARS", "MAX_ORDER_SIZE_CONTRACTS",
        "NO_TRADE_WINDOW_SECONDS",
    ]
    for k in risk_keys:
        if k in values:
            lines.append(f"{k}={values[k]}")

    lines.append("")
    lines.append("# ── Market-Making ────────────────────────────────────────────────────")
    mm_keys = [
        "MM_ENABLED", "MM_QUOTE_SIZE_CONTRACTS", "MM_EDGE_CENTS",
        "MM_INVENTORY_SKEW", "MM_CANCEL_REQUOTE_MS", "MM_ONLY_WHEN_VOL_BELOW",
    ]
    for k in mm_keys:
        if k in values:
            lines.append(f"{k}={values[k]}")

    lines.append("")
    lines.append("# ── Sniper ───────────────────────────────────────────────────────────")
    sniper_keys = [
        "SNIPER_ENABLED", "SNIPER_MIN_EDGE_CENTS", "SNIPER_MAX_SLIPPAGE_CENTS",
        "SNIPER_ORDER_TIF", "SNIPER_COOLDOWN_SECONDS",
    ]
    for k in sniper_keys:
        if k in values:
            lines.append(f"{k}={values[k]}")

    # Any remaining keys not yet written
    written = set(cred_keys + market_keys + risk_keys + mm_keys + sniper_keys)
    extras = {k: v for k, v in values.items() if k not in written}
    if extras:
        lines.append("")
        lines.append("# ── Additional ───────────────────────────────────────────────────────")
        for k, v in extras.items():
            lines.append(f"{k}={v}")

    lines.append("")
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")


def run_setup_wizard(existing: dict[str, str] | None = None) -> dict[str, str]:
    """Interactive wizard that collects Kalshi config values."""
    existing = existing or {}

    print()
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │           Kalshi BTC Bot — First-Time Setup             │")
    print("  └─────────────────────────────────────────────────────────┘")
    print()
    print("  We need a few settings to connect to your Kalshi account.")
    print("  Press Enter to accept the [default] values shown.")
    print()

    values: dict[str, str] = {}

    # ── Credentials ──
    print("  ── Kalshi Credentials ──")
    values["ENVIRONMENT"] = prompt_choice(
        "Environment", ["demo", "prod"],
        default=existing.get("ENVIRONMENT", "demo"),
    )
    values["KALSHI_KEY_ID"] = prompt(
        "Kalshi Key ID",
        default=existing.get("KALSHI_KEY_ID", ""),
        required=True,
    )

    key_path_default = existing.get("KALSHI_PRIVATE_KEY_PATH", "./kalshi.key")
    values["KALSHI_PRIVATE_KEY_PATH"] = prompt(
        "Path to RSA private key file (.key or .pem)",
        default=key_path_default,
        required=True,
    )

    # Validate the key path exists
    key_p = Path(values["KALSHI_PRIVATE_KEY_PATH"])
    if not key_p.is_absolute():
        key_p = PROJECT_ROOT / key_p
    if not key_p.exists():
        warn(f"Key file not found at: {key_p}")
        print("    You can place it there later, but the bot won't start without it.")

    print()
    print("  ── Safety ──")
    live = prompt_bool(
        "Enable LIVE trading? (real money — only say yes if you understand the risk)",
        default=existing.get("LIVE_TRADING", "false").lower() in ("true", "1", "yes"),
    )
    values["LIVE_TRADING"] = "true" if live else "false"

    # ── Optional: risk settings ──
    print()
    customize_risk = prompt_bool("Customize risk / strategy settings?", default=False)

    # Start with defaults from .env.example or hardcoded
    defaults = read_env_file(ENV_EXAMPLE) if ENV_EXAMPLE.exists() else {}
    # Merge any existing values (so reconfigure preserves old values)
    for k, v in defaults.items():
        if k not in values:
            values[k] = existing.get(k, v)

    if customize_risk:
        print()
        print("  ── Risk Settings ──")
        values["DAILY_STOP_DOLLARS"] = prompt(
            "Daily Stop Loss ($)", default=existing.get("DAILY_STOP_DOLLARS", "200")
        )
        values["MAX_GROSS_EXPOSURE_DOLLARS"] = prompt(
            "Max Gross Exposure ($)", default=existing.get("MAX_GROSS_EXPOSURE_DOLLARS", "250")
        )
        values["MAX_NET_EXPOSURE_DOLLARS"] = prompt(
            "Max Net Exposure ($)", default=existing.get("MAX_NET_EXPOSURE_DOLLARS", "150")
        )
        values["MAX_EXPOSURE_PER_MARKET_DOLLARS"] = prompt(
            "Max Per-Market Exposure ($)", default=existing.get("MAX_EXPOSURE_PER_MARKET_DOLLARS", "125")
        )
        values["MAX_ORDER_SIZE_CONTRACTS"] = prompt(
            "Max Order Size (contracts)", default=existing.get("MAX_ORDER_SIZE_CONTRACTS", "25")
        )

        print()
        print("  ── Strategy Toggles ──")
        mm = prompt_bool("Enable Market Making?", default=existing.get("MM_ENABLED", "true").lower() in ("true", "1"))
        values["MM_ENABLED"] = "true" if mm else "false"
        sniper = prompt_bool("Enable Sniper?", default=existing.get("SNIPER_ENABLED", "true").lower() in ("true", "1"))
        values["SNIPER_ENABLED"] = "true" if sniper else "false"
    else:
        # Fill in remaining defaults that weren't in .env.example
        for k, v in existing.items():
            if k not in values:
                values[k] = v

    return values


def ensure_env() -> None:
    """Create or update .env via the setup wizard."""
    if ENV_FILE.exists():
        existing = read_env_file(ENV_FILE)
        print()
        print(f"  .env file found ({ENV_FILE.name}).")
        print(f"    Environment: {existing.get('ENVIRONMENT', '?')}")
        print(f"    Key ID:      {existing.get('KALSHI_KEY_ID', '?')[:12]}...")
        print(f"    Live:        {existing.get('LIVE_TRADING', 'false')}")
        reconfigure = prompt_bool("Reconfigure Kalshi settings?", default=False)
        if not reconfigure:
            success("Using existing .env configuration.")
            return
        values = run_setup_wizard(existing)
    else:
        values = run_setup_wizard()

    write_env_file(values)
    success(f".env written to {ENV_FILE}")


# ── Step 4: Launch GUI ──────────────────────────────────────────────────────

def launch_gui() -> None:
    """Launch the PySide6 desktop GUI."""
    info("Launching Kalshi BTC Bot GUI ...")
    print()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")

    # Attempt 1: run as module
    try:
        run_cmd(
            [str(VENV_PYTHON), "-m", GUI_MODULE],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        warn(f"Module launch failed ({exc}), trying file path ...")

    # Attempt 2: run file directly
    if GUI_FILE.exists():
        try:
            run_cmd(
                [str(VENV_PYTHON), str(GUI_FILE)],
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            warn(f"File launch failed: {exc}")

    # Attempt 3: search for any main.py in gui directories
    for candidate in (
        PROJECT_ROOT / "src" / "gui" / "main.py",
        PROJECT_ROOT / "gui" / "main.py",
        PROJECT_ROOT / "app" / "main.py",
    ):
        if candidate.exists():
            try:
                run_cmd(
                    [str(VENV_PYTHON), str(candidate)],
                    cwd=str(PROJECT_ROOT),
                    env=env,
                )
                return
            except Exception:
                pass

    error("Could not find or launch the GUI.")
    print(f"    Tried: python -m {GUI_MODULE}")
    print(f"    Tried: python {GUI_FILE}")
    print("    Please check that src/app_gui/main.py exists and PySide6 is installed.")
    sys.exit(1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║          Kalshi BTC Bot — One-Click Setup               ║")
    print("  ╚══════════════════════════════════════════════════════════╝")

    os.chdir(PROJECT_ROOT)

    ensure_venv()
    install_deps()
    ensure_env()
    launch_gui()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled by user.")
        sys.exit(0)
    except subprocess.CalledProcessError as exc:
        error(f"Command failed (exit code {exc.returncode}): {exc.cmd}")
        print("    Check the output above for details.")
        if IS_WINDOWS:
            input("\n  Press Enter to close ...")
        sys.exit(1)
    except Exception as exc:
        error(str(exc))
        if IS_WINDOWS:
            input("\n  Press Enter to close ...")
        sys.exit(1)
