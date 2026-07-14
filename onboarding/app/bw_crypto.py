"""Bitwarden/Vaultwarden client-side account crypto (PBKDF2 flavour).

Produces exactly the payload a real Bitwarden client sends to
POST /identity/accounts/register so Vaultwarden creates a fully usable
account whose master password unlocks the vault.
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.padding import PKCS7

PBKDF2_ITERATIONS = 600_000


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(prk)


def _enc_string(data: bytes, enc_key: bytes, mac_key: bytes) -> str:
    """EncString type 2: AES-256-CBC + HMAC-SHA256 -> '2.iv|ct|mac' (base64)."""
    iv = os.urandom(16)
    padder = PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    h = HMAC(mac_key, hashes.SHA256())
    h.update(iv + ct)
    mac = h.finalize()
    return f"2.{_b64(iv)}|{_b64(ct)}|{_b64(mac)}"


def build_register_payload(email: str, name: str, password: str,
                           iterations: int = PBKDF2_ITERATIONS) -> dict:
    email_norm = email.strip().lower()

    # 1. Master key from password + email salt
    master_key = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     email_norm.encode(), iterations, dklen=32)

    # 2. Master password hash sent to server (1 iteration over master_key salted by password)
    master_password_hash = _b64(
        hashlib.pbkdf2_hmac("sha256", master_key, password.encode(), 1, dklen=32)
    )

    # 3. Stretch master key via HKDF-Expand into enc + mac halves
    stretched_enc = _hkdf_expand(master_key, b"enc", 32)
    stretched_mac = _hkdf_expand(master_key, b"mac", 32)

    # 4. Random user symmetric key (32 enc + 32 mac), protected by stretched master key
    user_key = os.urandom(64)
    protected_key = _enc_string(user_key, stretched_enc, stretched_mac)

    # 5. RSA keypair; private key encrypted with the user symmetric key
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = rsa_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_der = rsa_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    encrypted_private_key = _enc_string(private_der, user_key[:32], user_key[32:])

    return {
        "email": email_norm,
        "name": name,
        "masterPasswordHash": master_password_hash,
        "masterPasswordHint": None,
        "key": protected_key,
        "kdf": 0,  # 0 = PBKDF2-SHA256
        "kdfIterations": iterations,
        "keys": {
            "publicKey": _b64(public_der),
            "encryptedPrivateKey": encrypted_private_key,
        },
    }
