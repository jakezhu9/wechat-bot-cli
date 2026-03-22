"""AES-128-ECB encryption/decryption helpers for CDN media.

The WeChat iLink CDN encrypts all uploaded media with AES-128-ECB using
PKCS#7 padding.  This module provides the encrypt/decrypt primitives and
a helper to compute the padded ciphertext size without actually encrypting.

The ``cryptography`` library is used when available (recommended).  A pure-
Python fallback is **not** provided; ``cryptography`` is a hard dependency.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding

# AES block size in bytes.
_AES_BLOCK_SIZE: int = 16


def aes_ecb_padded_size(plaintext_len: int) -> int:
    """Return the size of AES-128-ECB ciphertext for *plaintext_len* bytes.

    PKCS#7 padding always adds at least 1 byte and at most a full block::

        padded = plaintext_len + (block - (plaintext_len % block))

    Parameters
    ----------
    plaintext_len:
        Length of the plaintext in bytes.

    Returns
    -------
    int
        The ciphertext size in bytes.
    """
    pad_bytes = _AES_BLOCK_SIZE - (plaintext_len % _AES_BLOCK_SIZE)
    return plaintext_len + pad_bytes


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt *plaintext* with AES-128-ECB and PKCS#7 padding.

    Parameters
    ----------
    plaintext:
        Data to encrypt.
    key:
        16-byte AES key.

    Returns
    -------
    bytes
        The ciphertext (always a multiple of 16 bytes).
    """
    # Apply PKCS#7 padding.
    padder = sym_padding.PKCS7(_AES_BLOCK_SIZE * 8).padder()
    padded = padder.update(plaintext) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-ECB *ciphertext* and strip PKCS#7 padding.

    Parameters
    ----------
    ciphertext:
        Encrypted data (must be a multiple of 16 bytes).
    key:
        16-byte AES key.

    Returns
    -------
    bytes
        The original plaintext.
    """
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()

    # Remove PKCS#7 padding.
    unpadder = sym_padding.PKCS7(_AES_BLOCK_SIZE * 8).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
