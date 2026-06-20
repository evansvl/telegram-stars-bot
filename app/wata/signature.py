"""Verification of WATA webhook signatures.

WATA signs the raw webhook body with RSA (SHA-512) and sends the base64
signature in the ``X-Signature`` header. The public key is fetched from
``WATA_PUBLIC_KEY_URL`` and cached for the process lifetime.
"""

from __future__ import annotations

import base64
import json
import logging

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)


class SignatureVerifier:
    """Fetches and caches the WATA public key, then verifies SHA512withRSA."""

    def __init__(self, public_key_url: str, session: aiohttp.ClientSession) -> None:
        self._url = public_key_url
        self._session = session
        self._public_key: rsa.RSAPublicKey | None = None

    async def _load_key(self) -> rsa.RSAPublicKey | None:
        """Fetch the PEM/DER public key from WATA. Returns None on failure."""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with self._session.get(self._url, timeout=timeout) as resp:
                resp.raise_for_status()
                raw = await resp.read()
        except aiohttp.ClientError as exc:
            logger.warning("failed to fetch WATA public key: %s", exc)
            return None

        # WATA returns the key wrapped in JSON: {"value": "-----BEGIN PUBLIC KEY-----\n..."}.
        # Fall back to a raw PEM/DER body for robustness.
        candidates: list[bytes] = []
        text = raw.decode("utf-8", errors="ignore").strip()
        try:
            parsed = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            for field in ("value", "publicKey", "key"):
                value = parsed.get(field)
                if isinstance(value, str) and value:
                    candidates.append(value.encode())
                    break
        elif isinstance(parsed, str) and parsed:
            candidates.append(parsed.encode())
        candidates.append(raw)

        key = None
        for candidate in candidates:
            try:
                key = serialization.load_pem_public_key(candidate)
                break
            except ValueError:
                try:
                    key = serialization.load_der_public_key(candidate)
                    break
                except ValueError:
                    continue

        if key is None:
            logger.error("WATA public key is neither valid PEM nor DER")
            return None
        if not isinstance(key, rsa.RSAPublicKey):
            logger.error("WATA public key is not an RSA key")
            return None
        return key

    async def public_key(self) -> rsa.RSAPublicKey | None:
        if self._public_key is None:
            self._public_key = await self._load_key()
        return self._public_key

    async def verify(self, body: bytes, signature_header: str | None) -> bool:
        """Verify ``signature_header`` (base64) against the raw ``body``.

        Returns True only if a key is available and the signature matches.
        """
        if not signature_header:
            return False
        key = await self.public_key()
        if key is None:
            return False
        try:
            signature = base64.b64decode(signature_header)
        except (ValueError, TypeError):
            logger.warning("X-Signature is not valid base64")
            return False
        try:
            key.verify(signature, body, padding.PKCS1v15(), hashes.SHA512())
        except InvalidSignature:
            return False
        return True
