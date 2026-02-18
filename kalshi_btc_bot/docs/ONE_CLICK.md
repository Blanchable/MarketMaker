# One-Click Setup Guide

Get the Kalshi BTC Bot running without any manual `pip` commands or `.env` file editing.

---

## Windows

1. Unzip the repository (or clone it).
2. **Double-click** `RUN_ME_FIRST.bat`.
3. The script will:
   - Check that Python 3.11+ is installed.
   - Create a virtual environment (`.venv/`).
   - Install all dependencies automatically.
   - Run the **setup wizard** (first time only) to collect your Kalshi credentials.
   - Launch the desktop GUI.
4. On subsequent runs, double-click the same BAT file. It will skip installation and prompts, and go straight to the GUI.

### Python not found?

If you see *"Python is not installed"*, download Python from [python.org](https://www.python.org/downloads/) and make sure to check **"Add Python to PATH"** during installation.

---

## macOS / Linux

1. Open a terminal in the repository folder.
2. Make the script executable (first time only):
   ```bash
   chmod +x run_me_first.sh
   ```
3. Run it:
   ```bash
   ./run_me_first.sh
   ```
4. Same flow as Windows: creates venv, installs deps, runs wizard, launches GUI.

### Python not found?

- **macOS (Homebrew):** `brew install python@3.12`
- **Ubuntu/Debian:** `sudo apt install python3.12 python3.12-venv`

---

## First-Run Setup Wizard

On the first run (when no `.env` file exists), the script will prompt you for:

| Prompt | Description | Default |
|---|---|---|
| **Environment** | `demo` or `prod` | `demo` |
| **Kalshi Key ID** | Your API key ID from Kalshi | *(required)* |
| **Private Key Path** | File path to your RSA `.key` or `.pem` file | `./kalshi.key` |
| **Enable LIVE trading** | Whether to trade with real money | `No` (false) |
| **Customize risk settings** | Optionally change risk limits and strategy toggles | `No` |

All values are saved to a `.env` file in the project root. This file is **never committed to git** (it's in `.gitignore`).

---

## Where is the .env stored?

The `.env` file is created at the root of the project directory, next to `pyproject.toml`:

```
kalshi_btc_bot/
  .env          <── your settings (auto-generated, git-ignored)
  .env.example  <── template with all available options
  pyproject.toml
  ...
```

---

## Reconfiguring

If you run the setup script again and a `.env` already exists, you will see:

```
  .env file found (.env).
    Environment: demo
    Key ID:      abc123def4...
    Live:        false
  Reconfigure Kalshi settings? (y/N):
```

- Press **Enter** (or type `n`) to keep existing settings and go straight to the GUI.
- Type `y` to re-enter your settings.

---

## Safety

- **LIVE_TRADING defaults to `false`**. You must explicitly type `y` when asked about live trading to enable it.
- The private key **file path** is stored in `.env`, but the key contents are never printed to the console or stored in any config file.
- The `.env` file is listed in `.gitignore` and will not be committed to version control.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Python not found | Install Python 3.11+ and add to PATH |
| `pip install` fails | Check internet connection; try running the script again |
| PySide6 display errors | On Linux: install `libEGL` (`sudo apt install libegl1`) |
| Key file not found | Place your `.key` file at the path you specified, then relaunch |
| Want to change settings | Run the setup script again and choose "Reconfigure" |
