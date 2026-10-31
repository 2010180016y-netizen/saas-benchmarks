"""
SlotBridge Korea — Field-Level Encryption & PII Masking Engine (SBK-FIX-03)
Implements standard AES-256-GCM (Authenticated Encryption with Associated Data)
and Zero-Plaintext Storage. Compatible with Cloudflare Worker Web Crypto API standard.
Enforces Fail-Closed security when encryption keys are missing or invalid.
"""

import os
import hmac
import hashlib
import secrets
import base64
import re
from typing import Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class CryptoError(Exception):
    """Raised when cryptographic operations fail or security constraints are violated (Fail-Closed)."""
    pass

def get_encryption_key(key: Optional[str] = None) -> bytes:
    """
    Resolves the 256-bit (32 bytes) encryption key.
    Enforces Fail-Closed: raises CryptoError if SBK_ENCRYPTION_KEY is missing.
    """
    raw_key = key or os.environ.get("SBK_ENCRYPTION_KEY")
    if not raw_key:
        raise CryptoError("MISSING_ENCRYPTION_KEY: SBK_ENCRYPTION_KEY environment variable is required (Fail-Closed).")

    if isinstance(raw_key, bytes):
        if len(raw_key) == 32:
            return raw_key
        return hashlib.sha256(raw_key).digest()

    raw_str = str(raw_key).strip()
    if len(raw_str) == 64:
        try:
            return bytes.fromhex(raw_str)
        except ValueError:
            pass

    # Deterministic SHA-256 derivation to guarantee exactly 32 bytes for AES-256
    return hashlib.sha256(raw_str.encode('utf-8')).digest()

def mask_phone(phone: str) -> str:
    """Masks phone number: 010-1234-5678 -> 010-****-5678"""
    if not phone:
        return ""
    clean = re.sub(r'[^0-9]', '', phone)
    if len(clean) == 11 and clean.startswith("010"):
        return f"{clean[:3]}-****-{clean[7:]}"
    elif len(clean) >= 10:
        return f"{clean[:3]}-****-{clean[-4:]}"
    return "010-****-****"

def mask_name(name: str) -> str:
    """Masks Korean customer name: 김관악 -> 김*악, 김동 -> 김*"""
    if not name:
        return ""
    name = name.strip()
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return f"{name[0]}*"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}"

def hash_phone(phone: str) -> str:
    """Deterministic SHA-256 hash for deduplication and rate-limiting without storing plaintext."""
    clean = re.sub(r'[^0-9]', '', phone)
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()

def _keystream_generator(key_bytes: bytes, nonce: bytes):
    """Legacy v1 helper for backward-compatible migration only."""
    counter = 0
    while True:
        counter_bytes = counter.to_bytes(8, byteorder='big')
        block = hmac.new(key_bytes, nonce + counter_bytes, hashlib.sha256).digest()
        for b in block:
            yield b
        counter += 1

def encrypt_pii(plaintext: str, key: Optional[str] = None) -> str:
    """
    Encrypts plaintext using standard AES-256-GCM.
    Payload format: base64(version(0x02, 1B) + iv(12B) + ciphertext_with_auth_tag(16B))
    """
    if not plaintext:
        return ""
    key_bytes = get_encryption_key(key)
    iv = secrets.token_bytes(12) # 96-bit standard nonce for GCM
    version = b'\x02'

    try:
        aesgcm = AESGCM(key_bytes)
        # encrypt(nonce, data, associated_data)
        ct_with_tag = aesgcm.encrypt(iv, plaintext.encode('utf-8'), version)
        payload = version + iv + ct_with_tag
        return base64.b64encode(payload).decode('ascii')
    except Exception as e:
        if isinstance(e, CryptoError):
            raise
        raise CryptoError(f"Encryption failed: {str(e)}")

def decrypt_pii(ciphertext_b64: str, key: Optional[str] = None) -> str:
    """
    Decrypts authenticated ciphertext and verifies 16-byte GCM integrity tag.
    Raises CryptoError on tampering or invalid key.
    """
    if not ciphertext_b64:
        return ""
    key_bytes = get_encryption_key(key)
    try:
        raw = base64.b64decode(ciphertext_b64.encode('ascii'))
        if len(raw) < 1 + 12 + 16:
            raise CryptoError("CIPHERTEXT_TOO_SHORT: Invalid ciphertext payload length")

        version = raw[0:1]
        if version == b'\x02': # Standard AES-256-GCM
            iv = raw[1:13]
            ct_with_tag = raw[13:]
            aesgcm = AESGCM(key_bytes)
            pt_bytes = aesgcm.decrypt(iv, ct_with_tag, version)
            return pt_bytes.decode('utf-8')
        elif version == b'\x01': # Backward-compatible migration support for legacy v1
            iv = raw[1:13]
            tag = raw[-16:]
            ct_bytes = raw[13:-16]
            expected_tag = hmac.new(key_bytes, version + iv + ct_bytes, hashlib.sha256).digest()[:16]
            if not hmac.compare_digest(tag, expected_tag):
                raise CryptoError("TAMPERED_CIPHERTEXT: Legacy v1 integrity check failed")
            ks = _keystream_generator(key_bytes, iv)
            pt_bytes = bytes([b ^ next(ks) for b in ct_bytes])
            return pt_bytes.decode('utf-8')
        else:
            raise CryptoError(f"UNSUPPORTED_VERSION: Unknown ciphertext version byte {version.hex()}")
    except Exception as e:
        if isinstance(e, CryptoError):
            raise
        raise CryptoError(f"TAMPERED_CIPHERTEXT: Decryption failed ({str(e)})")
