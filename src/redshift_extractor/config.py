from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv

import redshift_extractor.secret_loader as _secret_loader
from redshift_extractor.types import RedshiftConfig, SSHConfig

_REDSHIFT_KEY_RE = re.compile(r"^REDSHIFT__(?P<alias>[A-Za-z0-9_-]+)__(?P<field>[A-Z_]+)$")
_REQUIRED_RS_FIELDS = {"HOST", "PORT", "DBNAME"}
_CREDENTIAL_ENV_FIELD = "CREDENTIALS_ENV"
_get_env_value = _secret_loader.read_system_env_value
_read_windows_env_from_registry = _secret_loader.read_windows_env_value_from_registry


def _find_env_file() -> Path:
    """
    Encuentra .env.redshift_extractor sin depender del cwd del notebook.

    Orden:
    1) REDSHIFT_EXTRACTOR_ENV_FILE (si esta seteado)
    2) Busca hacia arriba desde el directorio del paquete hasta 8 niveles
       (cubre editable installs: <repo>/src/redshift_extractor/*.py)
    """
    override = os.getenv("REDSHIFT_EXTRACTOR_ENV_FILE")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"REDSHIFT_EXTRACTOR_ENV_FILE apunta a un archivo inexistente: {path}")
        return path

    current = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = current / ".env.redshift_extractor"
        if candidate.exists():
            return candidate
        current = current.parent

    raise FileNotFoundError(
        "No se encontro .env.redshift_extractor.\n"
        "Colocalo en la raiz del repo o define REDSHIFT_EXTRACTOR_ENV_FILE con ruta absoluta."
    )


def _load_own_env() -> None:
    env_path = _find_env_file()
    load_dotenv(dotenv_path=env_path, override=False)


def _resolve_rs_credentials(alias: str, fields: Dict[str, str]) -> Tuple[str, str]:
    user = fields.get("USER")
    password = fields.get("PASSWORD")
    credentials_env = fields.get(_CREDENTIAL_ENV_FIELD)

    if credentials_env:
        user, password = _secret_loader.resolve_secret_reference(credentials_env.strip())

    missing = [name for name, value in (("USER", user), ("PASSWORD", password)) if not value]
    if missing:
        raise ValueError(
            f"Config Redshift incompleta para alias '{alias}'. Faltan: {missing}. "
            f"Define USER/PASSWORD o {_CREDENTIAL_ENV_FIELD}."
        )

    return str(user), str(password)


def load_config() -> Tuple[SSHConfig, Dict[str, RedshiftConfig]]:
    """
    Carga unicamente configuracion desde .env.redshift_extractor.
    No carga .env del proyecto host explicitamente.
    """
    _load_own_env()

    ssh_host = os.getenv("SSH_HOST")
    ssh_port = int(os.getenv("SSH_PORT", "22"))
    ssh_user = os.getenv("SSH_USER")
    ssh_pkey_path = os.getenv("SSH_PKEY_PATH")

    missing = [
        key
        for key, value in (
            ("SSH_HOST", ssh_host),
            ("SSH_USER", ssh_user),
            ("SSH_PKEY_PATH", ssh_pkey_path),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Faltan variables SSH en .env.redshift_extractor: {missing}")

    ssh = SSHConfig(
        host=ssh_host,  # type: ignore[arg-type]
        port=ssh_port,
        user=ssh_user,  # type: ignore[arg-type]
        pkey_path=ssh_pkey_path,  # type: ignore[arg-type]
    )

    buckets: Dict[str, Dict[str, str]] = {}
    for key, value in os.environ.items():
        match = _REDSHIFT_KEY_RE.match(key)
        if not match:
            continue
        alias = match.group("alias").lower()
        field = match.group("field")
        buckets.setdefault(alias, {})[field] = value

    if not buckets:
        raise ValueError("No se encontraron variables REDSHIFT__<alias>__* en .env.redshift_extractor")

    rs_map: Dict[str, RedshiftConfig] = {}
    for alias, fields in buckets.items():
        missing_rs = _REQUIRED_RS_FIELDS - set(fields.keys())
        if missing_rs:
            raise ValueError(f"Config Redshift incompleta para alias '{alias}'. Faltan: {sorted(missing_rs)}")

        user, password = _resolve_rs_credentials(alias, fields)
        rs_map[alias] = RedshiftConfig(
            host=str(fields["HOST"]),
            port=int(fields["PORT"]),
            dbname=str(fields["DBNAME"]),
            user=user,
            password=password,
        )

    return ssh, rs_map
