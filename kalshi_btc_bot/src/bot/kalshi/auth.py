"""Kalshi API authentication – RSA-PSS signature generation."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from bot.infra.log import get_logger
from bot.infra.time import now_sec

log = get_logger(__name__)


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

    def sign(self, timestamp: int, method: str, path: str) -> str:
        """Produce base64-encoded RSA-PSS signature of (timestamp + METHOD + path).

        Parameters
        ----------
        timestamp : epoch seconds (int)
        method : HTTP method uppercase, e.g. ``GET``
        path : request path starting with ``/``, e.g. ``/trade-api/v2/markets``
        """
        message = f"{timestamp}{method}{path}".encode()
        sig_bytes = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig_bytes).decode()

    def headers(self, method: str, path: str) -> dict[str, str]:
        """Return a dict of authentication headers for one request."""
        ts = now_sec()
        sig = self.sign(ts, method.upper(), path)
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
        }
