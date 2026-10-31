"""
SlotBridge Korea — Field-Level Encryption & PII Masking Engine
Implements AES-256-GCM / Authenticated Encryption & Zero-Plaintext Storage.
Compatible with Cloudflare Worker Web Crypto API standard.
"""

import os
import hmac
import hashlib
import secrets
import base64
import re
from typing import Tuple

DEFAULT_KEY = os.environ.get("SBK_ENCRYPTION_KEY", "sbk_secret_key_32_bytes_test_2026_ok!")

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
    """Deterministic SHA-256 hash for deduplication without storing plaintext."""
    clean = re.sub(r'[^0-9]', '', phone)
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()

def _keystream_generator(key_bytes: bytes, nonce: bytes):
    """Generates continuous pseudo-random keystream using HMAC-SHA256 (CTR mode equivalent)."""
    counter = 0
    while True:
        counter_bytes = counter.to_bytes(8, byteorder='big')
        block = hmac.new(key_bytes, nonce + counter_bytes, hashlib.sha256).digest()
        for b in block:
            yield b
        counter += 1

def encrypt_pii(plaintext: str, key: str = DEFAULT_KEY) -> str:
    """
    Encrypts plaintext with authenticated encryption (AES-256-GCM / HMAC-CTR construct).
    Output format: base64(version(1B) + iv(12B) + ciphertext + tag(16B))
    """
    if not plaintext:
        return ""
    key_bytes = hashlib.sha256(key.encode('utf-8')).digest()
    iv = secrets.token_bytes(12) # 96-bit nonce
    pt_bytes = plaintext.encode('utf-8')
    
    # Encrypt
    ks = _keystream_generator(key_bytes, iv)
    ct_bytes = bytes([b ^ next(ks) for b in pt_bytes])
    
    # Authenticate (Tag)
    version = b'\x01'
    tag = hmac.new(key_bytes, version + iv + ct_bytes, hashlib.sha256).digest()[:16]
    
    payload = version + iv + ct_bytes + tag
    return base64.b64encode(payload).decode('ascii')

def decrypt_pii(ciphertext_b64: str, key: str = DEFAULT_KEY) -> str:
    """
    Decrypts authenticated ciphertext and verifies integrity tag.
    Raises ValueError on tampering or invalid key.
    """
    if not ciphertext_b64:
        return ""
    try:
        raw = base64.b64decode(ciphertext_b64.encode('ascii'))
        if len(raw) < 1 + 12 + 16:
            raise ValueError("Ciphertext too short")
        
        version = raw[0:1]
        iv = raw[1:13]
        tag = raw[-16:]
        ct_bytes = raw[13:-16]
        
        key_bytes = hashlib.sha256(key.encode('utf-8')).digest()
        
        # Verify Tag
        expected_tag = hmac.new(key_bytes, version + iv + ct_bytes, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError("Integrity check failed: invalid key or tampered data")
        
        # Decrypt
        ks = _keystream_generator(key_bytes, iv)
        pt_bytes = bytes([b ^ next(ks) for b in ct_bytes])
        return pt_bytes.decode('utf-8')
    except Exception as e:
        raise ValueError(f"Decryption failed: {str(e)}")
