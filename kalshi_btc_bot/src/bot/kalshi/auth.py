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
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ec, ed25519

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
        self._validate_key()

    @staticmethod
    def _load_key(path: str | Path) -> rsa.RSAPrivateKey:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Private key file not found: {p}")

        raw = p.read_bytes()

        # Strip UTF-8 BOM if present (common on Windows)
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        # Normalise Windows line endings
        raw = raw.replace(b"\r\n", b"\n")

        # Try PEM (PKCS#8 or PKCS#1)
        try:
            key = serialization.load_pem_private_key(raw, password=None)
        except (ValueError, TypeError):
            # Try DER as fallback
            try:
                key = serialization.load_der_private_key(raw, password=None)
            except Exception:
                raise ValueError(
                    f"Could not load private key from {p}. "
                    "Ensure it is a PEM-encoded RSA private key (PKCS#8 or PKCS#1)."
                )

        if not isinstance(key, rsa.RSAPrivateKey):
            actual = type(key).__name__
            raise TypeError(
                f"Kalshi requires an RSA private key, but loaded a {actual}. "
                "Re-generate your API key on Kalshi to get an RSA key."
            )
        return key

    def _validate_key(self) -> None:
        """Sanity-check: sign+verify a test message at init time."""
        test_msg = b"kalshi-auth-selftest"
        try:
            sig = self._private_key.sign(
                test_msg,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
            self._private_key.public_key().verify(
                sig,
                test_msg,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise ValueError(
                f"RSA key self-test failed: {exc}. The key file may be corrupted."
            ) from exc

        key_size = self._private_key.key_size
        log.info(
            "Auth initialised: key_id=%s... key_size=%d-bit",
            self.key_id[:12], key_size,
        )

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
