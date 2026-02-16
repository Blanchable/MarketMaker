"""Kalshi API authentication – RSA-PSS signature generation.

Matches the official kalshi-python SDK signing behaviour:
  - Timestamp in **milliseconds** (not seconds).
  - RSA-PSS with salt_length = DIGEST_LENGTH (32 bytes for SHA-256).
  - Message = ``str(ts_ms) + METHOD + path`` (path **without** query string).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from bot.infra.log import get_logger

log = get_logger(__name__)


def _now_ms() -> int:
    """Current epoch in milliseconds."""
    return int(time.time() * 1000)


class KalshiAuth:
    """Generates authentication headers for Kalshi REST and WS calls."""

    def __init__(self, key_id: str, private_key_path: str | Path) -> None:
        self.key_id = key_id
        self._private_key = self._load_key(private_key_path)

    @staticmethod
    def _load_key(path: str | Path) -> rsa.RSAPrivateKey:
        pem_data = Path(path).read_bytes()
        key = serialization.load_pem_private_key(pem_data, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError("Kalshi requires an RSA private key")
        return key

    def sign(self, timestamp_ms: int, method: str, path: str) -> str:
        """Produce base64-encoded RSA-PSS signature.

        Parameters
        ----------
        timestamp_ms : epoch **milliseconds** (int).
        method : HTTP method uppercase, e.g. ``GET``.
        path : request path starting with ``/``,
               e.g. ``/trade-api/v2/portfolio/balance``.
               Must **not** include the query string.
        """
        message = f"{timestamp_ms}{method}{path}".encode("utf-8")
        sig_bytes = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig_bytes).decode("utf-8")

    def headers(self, method: str, path: str) -> dict[str, str]:
        """Return authentication headers for one request.

        ``path`` must be the URL path **without** query parameters.
        """
        ts_ms = _now_ms()
        sig = self.sign(ts_ms, method.upper(), path)
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
        }
