"""Per-project cosign key management via project-namespaced OpenBao."""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class CosignKey:
    key_name: str
    private_key: str
    public_key: str
    password: str | None = None
    created_at: str = ""


def _scoped_path(project_id: str, key_name: str) -> str:
    return f"projects/{project_id}/cosign/{key_name}"


def write_cosign_key(
    mgr: Any,
    project_id: str,
    key_name: str,
    private_key: str,
    public_key: str,
    password: str | None = None,
) -> None:
    mgr.write_secret(
        _scoped_path(project_id, key_name),
        {
            "key_name": key_name,
            "private_key": private_key,
            "public_key": public_key,
            "password": password,
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )


def read_cosign_key(mgr: Any, project_id: str, key_name: str) -> CosignKey | None:
    data = mgr.read_secret(_scoped_path(project_id, key_name))
    if data is None:
        return None
    return CosignKey(
        key_name=data.get("key_name", key_name),
        private_key=data.get("private_key", ""),
        public_key=data.get("public_key", ""),
        password=data.get("password"),
        created_at=data.get("created_at", ""),
    )


def delete_cosign_key(mgr: Any, project_id: str, key_name: str) -> None:
    mgr.delete_secret(_scoped_path(project_id, key_name))


def generate_cosign_key(
    key_name: str,
    output_dir: str | None = None,
    password: str | None = None,
) -> CosignKey:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key_obj = ec.generate_private_key(ec.SECP256R1())
    public_key_obj = private_key_obj.public_key()

    encryption = (
        serialization.BestAvailableEncryption(password.encode())
        if password
        else serialization.NoEncryption()
    )
    private_pem = private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode()
    public_pem = public_key_obj.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "cosign.key"), "w") as f:
            f.write(private_pem)
        with open(os.path.join(output_dir, "cosign.pub"), "w") as f:
            f.write(public_pem)

    return CosignKey(
        key_name=key_name,
        private_key=private_pem,
        public_key=public_pem,
        password=password,
    )


def generate_and_store_cosign_key(
    mgr: Any,
    project_id: str,
    key_name: str,
    output_dir: str | None = None,
    password: str | None = None,
) -> CosignKey:
    key = generate_cosign_key(key_name=key_name, output_dir=output_dir, password=password)
    write_cosign_key(
        mgr=mgr,
        project_id=project_id,
        key_name=key_name,
        private_key=key.private_key,
        public_key=key.public_key,
        password=key.password,
    )
    return key
