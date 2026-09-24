"""Objektlagring för originalfiler och bilagor, med kryptering per byrå.

Varje byrå har en egen datanyckel (DEK) som krypteras med en huvudnyckel (KEK, i produktion
från Key Vault/KMS). Raderas byråns nyckel blir alla dess filer oläsbara – "crypto-shredding".
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

from redovisningai.config import Settings, get_settings


def _kek(master_key: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(master_key.encode()).digest())
    return Fernet(key)


def new_encrypted_dek(settings: Settings | None = None) -> str:
    """Skapa en ny datanyckel för en byrå, krypterad med huvudnyckeln."""
    s = settings or get_settings()
    return _kek(s.master_key).encrypt(Fernet.generate_key()).decode()


def dek_cipher(encrypted_dek: str, settings: Settings | None = None) -> Fernet:
    s = settings or get_settings()
    return Fernet(_kek(s.master_key).decrypt(encrypted_dek.encode()))


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("Ogiltig objektnyckel")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3ObjectStore:  # pragma: no cover - kräver S3/MinIO
    def __init__(self, settings: Settings) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()  # type: ignore[no-any-return]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


class EncryptedStore:
    """Krypterar innehållet med byråns datanyckel innan det lagras."""

    def __init__(self, inner: ObjectStore, encrypted_dek: str, settings: Settings | None = None) -> None:
        self.inner = inner
        self.cipher = dek_cipher(encrypted_dek, settings)

    def put(self, key: str, data: bytes) -> None:
        self.inner.put(key, self.cipher.encrypt(data))

    def get(self, key: str) -> bytes:
        return self.cipher.decrypt(self.inner.get(key))

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)


def get_object_store(settings: Settings | None = None) -> ObjectStore:
    s = settings or get_settings()
    if s.storage_backend == "s3":
        return S3ObjectStore(s)
    return LocalObjectStore(s.storage_path)
