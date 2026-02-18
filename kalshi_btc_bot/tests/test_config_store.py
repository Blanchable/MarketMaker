"""Tests for the GUI config store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app_gui.config_store import (
    _DEFAULTS,
    export_config,
    import_config,
    load_config,
    save_config,
)


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config storage to a temp directory."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("app_gui.config_store.config_path", lambda: config_file)
    return config_file


class TestConfigStore:
    def test_load_defaults_when_no_file(self, tmp_config: Path) -> None:
        cfg = load_config()
        assert cfg["environment"] == "demo"
        assert cfg["paper_mode"] is True
        assert cfg["daily_stop_dollars"] == 200.0

    def test_save_and_load(self, tmp_config: Path) -> None:
        cfg = {"environment": "prod", "key_id": "abc123", "daily_stop_dollars": 500}
        save_config(cfg)
        assert tmp_config.exists()

        loaded = json.loads(tmp_config.read_text())
        assert loaded["environment"] == "prod"
        assert loaded["key_id"] == "abc123"

    def test_never_stores_private_key_contents(self, tmp_config: Path) -> None:
        cfg = {"key_id": "test", "private_key_contents": "SECRET_DATA"}
        save_config(cfg)
        stored = json.loads(tmp_config.read_text())
        assert "private_key_contents" not in stored

    def test_export_import(self, tmp_path: Path, tmp_config: Path) -> None:
        cfg = {"environment": "demo", "daily_stop_dollars": 300}
        export_file = tmp_path / "exported.json"
        export_config(cfg, export_file)
        assert export_file.exists()

        imported = import_config(export_file)
        assert imported["daily_stop_dollars"] == 300
        assert imported["environment"] == "demo"

    def test_load_merges_with_defaults(self, tmp_config: Path) -> None:
        partial = {"environment": "prod"}
        tmp_config.write_text(json.dumps(partial))

        cfg = load_config()
        assert cfg["environment"] == "prod"
        assert cfg["paper_mode"] == _DEFAULTS["paper_mode"]
        assert cfg["max_order_size_contracts"] == _DEFAULTS["max_order_size_contracts"]
