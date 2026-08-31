from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ENV_FILE = REPO_ROOT / ".env.redshift_extractor"

#: Env minimo valido para los tests unitarios. Sin credenciales reales (K9).
MINIMAL_ENV = """
SSH_HOST=bastion.example.test
SSH_PORT=22
SSH_USER=tester
SSH_PKEY_PATH=C:\\keys\\id_rsa
SSH_LOCAL_PORT=0
DEFAULT_ALIAS=prod

REDSHIFT__prod__HOST=prod-cluster.example.test
REDSHIFT__prod__PORT=5439
REDSHIFT__prod__DBNAME=analytics
REDSHIFT__prod__USER=lector
REDSHIFT__prod__PASSWORD=pass-lectura

REDSHIFT__dev__HOST=dev-cluster.example.test
REDSHIFT__dev__PORT=5439
REDSHIFT__dev__DBNAME=analytics_dev
REDSHIFT__dev__USER=escritor
REDSHIFT__dev__PASSWORD=pa(ss)+wo|rd$
""".lstrip()


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch):
    """
    Deja el proceso sin estado entre tests.

    Importa: si no se borra REDSHIFT_EXTRACTOR_ENV_FILE, un test unitario podria
    acabar leyendo el env real del repo por la busqueda hacia arriba.
    """
    import logging

    from redshift_extractor import events as events_mod
    from redshift_extractor import tunnel as tunnel_mod

    for key in [name for name in os.environ if name.startswith("REDSHIFT__")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("REDSHIFT_EXTRACTOR_ENV_FILE", raising=False)
    monkeypatch.delenv("REDSHIFT_EXTRACTOR_LOG_LEVEL", raising=False)

    # `configure_logging()` (que corre en cada comando del CLI) le pone handler,
    # level y propagate=False al logger propio. Se restaura para que un test del CLI
    # no cambie lo que ve el siguiente.
    propio = logging.getLogger("redshift_extractor")
    log_state = (list(propio.handlers), propio.level, propio.propagate)

    yield

    try:
        tunnel_mod._close_all()
    finally:
        tunnel_mod._reset_for_tests()
        events_mod.clear_secrets()
        propio.handlers, propio.level, propio.propagate = log_state


@pytest.fixture
def minimal_env() -> str:
    return MINIMAL_ENV


@pytest.fixture
def write_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Escribe un env propio en tmp y apunta la variable de override hacia el."""

    def _write(content: str = MINIMAL_ENV, *, bom: bool = False, point_to_it: bool = True) -> Path:
        path = tmp_path / ".env.redshift_extractor"
        data = content.encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        path.write_bytes(data)
        if point_to_it:
            monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(path))
        return path

    return _write


# -----------------------------------------------------------------------------
# Tunel de prueba en proceso (servidor SSH + servidor que habla el protocolo pg)
# -----------------------------------------------------------------------------
@pytest.fixture
def fake_redshift():
    """
    Doble del cluster. Redshift habla el mismo protocolo de frontend que PostgreSQL,
    asi que el doble de la referencia sirve tal cual: contesta el SSLRequest que manda
    `probe_redshift`.
    """
    from fakepg import FakePostgres

    server = FakePostgres()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def client_key(tmp_path):
    from sshserver import generate_client_key

    return generate_client_key(tmp_path / "keys")


@pytest.fixture
def ssh_server(client_key):
    from sshserver import ForwardingSSHServer

    key, _path = client_key
    server = ForwardingSSHServer(allowed_pubkey=key)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def tunnel_env(tmp_path, monkeypatch, ssh_server, client_key, fake_redshift):
    """
    Env que apunta al servidor SSH de prueba y, del otro lado, al cluster falso.

    Permite ejercitar el tunel de verdad —handshake SSH y host key incluidos— sin el
    bastion ni el cluster (K2).
    """
    _key, key_path = client_key
    known_hosts = ssh_server.write_known_hosts(tmp_path / "known_hosts")

    def _write(*, local_port: int = 0, extra: str = "", pkey: Path = key_path) -> Path:
        content = textwrap.dedent(
            f"""
            SSH_HOST=127.0.0.1
            SSH_PORT={ssh_server.port}
            SSH_USER=tester
            SSH_PKEY_PATH={pkey}
            SSH_KNOWN_HOSTS_PATH={known_hosts}
            SSH_LOCAL_PORT={local_port}
            SSH_CONNECT_TIMEOUT_S=5
            DEFAULT_ALIAS=prod

            REDSHIFT__prod__HOST=127.0.0.1
            REDSHIFT__prod__PORT={fake_redshift.port}
            REDSHIFT__prod__DBNAME=analytics
            REDSHIFT__prod__USER=u
            REDSHIFT__prod__PASSWORD=p

            REDSHIFT__dev__HOST=127.0.0.1
            REDSHIFT__dev__PORT={fake_redshift.port}
            REDSHIFT__dev__DBNAME=analytics_dev
            REDSHIFT__dev__USER=u
            REDSHIFT__dev__PASSWORD=p
            """
        ).lstrip() + extra
        path = tmp_path / ".env.redshift_extractor"
        path.write_bytes(content.encode("utf-8"))
        monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(path))
        return path

    return _write


@pytest.fixture
def events_log():
    """Colector de eventos: devuelve (lista, callback)."""
    collected: list = []

    def collect(event: dict) -> None:
        collected.append(event)

    return collected, collect
