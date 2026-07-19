"""Bitwarden/Vaultwarden account-registration crypto, in pure Python.

This replaces the headless-browser approach previously used to register
Vaultwarden accounts. The reason a browser was used at all was the fear that a
hand-rolled reimplementation of Bitwarden's client-side key derivation could be
"subtly wrong in a way you can't detect" -- producing an account the customer's
real Bitwarden app can never unlock. That risk is real, so this module is:

  1. A faithful implementation of the exact scheme the Bitwarden web client uses
     (PBKDF2-SHA256 master key, HKDF-Expand key stretching, EncString type 2 =
     AES-256-CBC + HMAC-SHA256, RSA-2048 keypair), and
  2. Guarded by a self-test (verify_against_reference / the module test) that
     reproduces a request captured from the real web client bit-for-bit, so a
     regression in these primitives fails loudly instead of silently minting
     un-unlockable vaults.

Reference for the wire format is a real /identity/accounts/register/finish body
captured from the Vaultwarden 2025.12 web vault; see tests/ and the module
docstring in services/vaultwarden.py.
"""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.hashes import SHA256

# Bitwarden's default KDF: PBKDF2-SHA256. `kdf: 0` on the wire.
KDF_PBKDF2 = 0
DEFAULT_PBKDF2_ITERATIONS = 600_000


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def make_master_key(password: str, email: str, iterations: int) -> bytes:
    """The 32-byte master key: PBKDF2-SHA256 of the password, salted with the
    lowercased email (Bitwarden salts with the email, never the server URL --
    which is why registration is independent of the server's DOMAIN)."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), email.strip().lower().encode("utf-8"),
        iterations, dklen=32,
    )


def make_master_password_hash(master_key: bytes, password: str) -> str:
    """The auth hash sent to the server: one more PBKDF2 round of the master key
    salted with the password. Deterministic for a given (email, password,
    iterations) -- the property the self-test exploits."""
    h = hashlib.pbkdf2_hmac("sha256", master_key, password.encode("utf-8"), 1, dklen=32)
    return _b64(h)


def _stretch_master_key(master_key: bytes) -> tuple[bytes, bytes]:
    """HKDF-Expand (no extract; the master key is used directly as the PRK) into
    a 32-byte AES key and a 32-byte HMAC key, with info strings "enc"/"mac"."""
    enc = HKDFExpand(algorithm=SHA256(), length=32, info=b"enc").derive(master_key)
    mac = HKDFExpand(algorithm=SHA256(), length=32, info=b"mac").derive(master_key)
    return enc, mac


def _encstring_aescbc_hmac(plaintext: bytes, enc_key: bytes, mac_key: bytes, iv: bytes | None = None) -> str:
    """Produce an EncString of type 2 (AesCbc256_HmacSha256_B64):
        "2.<b64 iv>|<b64 ciphertext>|<b64 mac>"
    where mac = HMAC-SHA256(mac_key, iv || ciphertext). PKCS7 padding."""
    if iv is None:
        iv = os.urandom(16)
    pad_len = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    return f"2.{_b64(iv)}|{_b64(ct)}|{_b64(mac)}"


def decrypt_encstring(encstring: str, enc_key: bytes, mac_key: bytes) -> bytes:
    """Inverse of _encstring_aescbc_hmac, for the round-trip self-test. Verifies
    the MAC before decrypting."""
    type_prefix, rest = encstring.split(".", 1)
    if type_prefix != "2":
        raise ValueError(f"unsupported EncString type {type_prefix}")
    iv_b64, ct_b64, mac_b64 = rest.split("|")
    iv, ct, mac = base64.b64decode(iv_b64), base64.b64decode(ct_b64), base64.b64decode(mac_b64)
    expected = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("MAC mismatch")
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    return padded[: -padded[-1]]


def build_registration_keys(password: str, email: str, iterations: int = DEFAULT_PBKDF2_ITERATIONS) -> dict:
    """Build everything /identity/accounts/register/finish needs, exactly as the
    Bitwarden web client would:

      - masterPasswordHash  : PBKDF2(masterKey, password, 1)
      - userSymmetricKey    : random 64-byte user key, wrapped with the
                              HKDF-stretched master key (EncString type 2)
      - userAsymmetricKeys  : RSA-2048 keypair; public key as base64 SPKI DER,
                              private key (PKCS8 DER) wrapped with the user key
    """
    master_key = make_master_key(password, email, iterations)
    master_password_hash = make_master_password_hash(master_key, password)
    stretched_enc, stretched_mac = _stretch_master_key(master_key)

    # The user's symmetric key: 32-byte AES key || 32-byte MAC key.
    user_key = os.urandom(64)
    protected_user_key = _encstring_aescbc_hmac(user_key, stretched_enc, stretched_mac)

    # RSA keypair; private key is encrypted with the user's symmetric key.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    encrypted_private_key = _encstring_aescbc_hmac(private_der, user_key[:32], user_key[32:])

    return {
        "master_key": master_key,
        "user_key": user_key,
        "masterPasswordHash": master_password_hash,
        "userSymmetricKey": protected_user_key,
        "userAsymmetricKeys": {
            "publicKey": _b64(public_der),
            "encryptedPrivateKey": encrypted_private_key,
        },
        "kdf": KDF_PBKDF2,
        "kdfIterations": iterations,
    }
