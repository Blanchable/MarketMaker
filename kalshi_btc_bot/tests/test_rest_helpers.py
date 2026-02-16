"""Tests for REST client helper functions."""

from __future__ import annotations

from bot.kalshi.rest import _safe_list


class TestSafeList:
    def test_key_present_with_list(self) -> None:
        assert _safe_list({"items": [1, 2, 3]}, "items") == [1, 2, 3]

    def test_key_present_with_none(self) -> None:
        """Kalshi often returns {"series": null} – must not crash."""
        assert _safe_list({"series": None}, "series") == []

    def test_key_missing(self) -> None:
        assert _safe_list({}, "series") == []

    def test_key_present_empty_list(self) -> None:
        assert _safe_list({"events": []}, "events") == []
