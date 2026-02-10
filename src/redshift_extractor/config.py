from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv

from redshift_extractor.types import RedshiftConfig, SSHConfig

_REDSHIFT_KEY_RE = re.compile(r"^REDSHIFT__(?P<alias>[A-Za-z0-9_-]+)__(?P<field>[A-Z]+)$")
_REQUIRED_RS_FIELDS = {"HOST", "PORT", "DBNAME", "USER", "PASSWORD"}


def _find_env_file() -> Path:
    """
    Encuentra .env.redshift_extractor SIN depender del cwd del notebook.

    Orden:
    1) REDSHIFT_EXTRACTOR_ENV_FILE (si está seteado)
    2) Busca hacia arriba desde el directorio del paquete hasta 8 niveles
       (cubre editable installs: <repo>/src/redshift_extractor/*.py)
    """
    override = os.getenv("REDSHIFT_EXTRACTOR_ENV_FILE")
    if override:
        p = Path(override).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"REDSHIFT_EXTRACTOR_ENV_FILE apunta a un archivo inexistente: {p}")
        return p

    here = Path(__file__).resolve().parent
    cur = here
    for _ in range(8):
        candidate = cur / ".env.redshift_extractor"
        if candidate.exists():
            return candidate
        cur = cur.parent

    raise FileNotFoundError(
        "No se encontró .env.redshift_extractor.\n"
        "Colócalo en la raíz del repo o define REDSHIFT_EXTRACTOR_ENV_FILE con ruta absoluta."
    )


def _load_own_env() -> None:
    env_path = _find_env_file()
    load_dotenv(dotenv_path=env_path, override=False)


def load_config() -> Tuple[SSHConfig, Dict[str, RedshiftConfig]]:
    """
    Carga únicamente configuración desde .env.redshift_extractor.
    No carga .env del proyecto host explícitamente.
    """
    _load_own_env()

    # SSH
    ssh_host = os.getenv("SSH_HOST")
    ssh_port = int(os.getenv("SSH_PORT", "22"))
    ssh_user = os.getenv("SSH_USER")
    ssh_pkey_path = os.getenv("SSH_PKEY_PATH")

    missing = [k for k, v in [("SSH_HOST", ssh_host), ("SSH_USER", ssh_user), ("SSH_PKEY_PATH", ssh_pkey_path)] if not v]
    if missing:
        raise ValueError(f"Faltan variables SSH en .env.redshift_extractor: {missing}")

    ssh = SSHConfig(
        host=ssh_host,  # type: ignore[arg-type]
        port=ssh_port,
        user=ssh_user,  # type: ignore[arg-type]
        pkey_path=ssh_pkey_path,  # type: ignore[arg-type]
    )

    # Redshift configs
    buckets: Dict[str, Dict[str, str]] = {}
    for k, v in os.environ.items():
        m = _REDSHIFT_KEY_RE.match(k)
        if not m:
            continue
        alias = m.group("alias").lower()  # 🔥 normaliza para evitar MAYÚSCULAS de Windows
        field = m.group("field")
        buckets.setdefault(alias, {})[field] = v

    if not buckets:
        raise ValueError("No se encontraron variables REDSHIFT__<alias>__* en .env.redshift_extractor")

    rs_map: Dict[str, RedshiftConfig] = {}
    for alias, fields in buckets.items():
        missing_rs = _REQUIRED_RS_FIELDS - set(fields.keys())
        if missing_rs:
            raise ValueError(f"Config Redshift incompleta para alias '{alias}'. Faltan: {sorted(missing_rs)}")
        rs_map[alias] = RedshiftConfig(
            host=str(fields["HOST"]),
            port=int(fields["PORT"]),
            dbname=str(fields["DBNAME"]),
            user=str(fields["USER"]),
            password=str(fields["PASSWORD"]),
        )

    return ssh, rs_map