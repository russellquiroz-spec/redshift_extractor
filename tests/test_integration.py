"""
Integracion contra el bastion y el cluster real.

Se salta entera si no hay `.env.redshift_extractor` o si el bastion no responde, asi
que la suite sigue corriendo sin infraestructura (K2). El CI la excluye con
`-m "not integration"`.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional, Tuple

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ENV_FILE = REPO_ROOT / ".env.redshift_extractor"


def _real_ssh_host() -> Optional[Tuple[str, int]]:
    if not REAL_ENV_FILE.exists():
        return None
    from redshift_extractor.config import read_env_file

    try:
        values = read_env_file(REAL_ENV_FILE)
    except Exception:  # noqa: BLE001
        return None
    host = values.get("SSH_HOST")
    if not host:
        return None
    return host, int(values.get("SSH_PORT", "22"))


def _tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def real_env(clean_state, monkeypatch):
    """
    Apunta la config al env real del repo. Salta el test si no hay bastion alcanzable.

    Depende de `clean_state` a proposito, para que el borrado de la variable de
    override ocurra ANTES de que este fixture la ponga.
    """
    if not REAL_ENV_FILE.exists():
        pytest.skip(f"No existe {REAL_ENV_FILE.name}: no hay contra que integrar.")
    target = _real_ssh_host()
    if not target or not _tcp_open(*target):
        pytest.skip("El bastion no responde en el puerto SSH: integracion saltada.")
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(REAL_ENV_FILE))
    return REAL_ENV_FILE


def test_ping_reporta_lo_que_ve_el_servidor(real_env):
    """E6: base, usuario y version salen del cluster, no de la config."""
    from redshift_extractor import list_aliases, ping

    alias = list_aliases()[0]
    resultado = ping(alias)

    assert resultado["ok"] is True
    assert resultado["alias"] == alias
    assert resultado["database"]
    assert resultado["user"]
    assert resultado["tunnel_port"] > 0
    assert resultado["latency_ms"] > 0
    assert "db" not in resultado


def test_ping_no_expone_credenciales(real_env):
    from redshift_extractor import config as config_mod
    from redshift_extractor import list_aliases, ping

    alias = list_aliases()[0]
    _app, _ssh, _resolved, cfg = config_mod.resolve(alias)
    texto = " ".join(str(valor) for valor in ping(alias).values())
    assert cfg.password not in texto


def test_extract_sql_devuelve_dataframe(real_env):
    from redshift_extractor import extract_sql, list_aliases

    # Alias explicito: el env real de esta maquina es anterior a DEFAULT_ALIAS.
    df = extract_sql("select 1 as uno", alias=list_aliases()[0])
    assert list(df.columns) == ["uno"]
    assert df.iloc[0]["uno"] == 1
