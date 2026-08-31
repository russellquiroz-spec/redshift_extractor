"""
Convivencia (seccion H): garantias por construccion, verificables dentro de este repo
sin tener instaladas las librerias hermanas.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

PAQUETE = Path(__file__).resolve().parents[1] / "src" / "redshift_extractor"
MODULOS = sorted(PAQUETE.glob("*.py"))


def _fuente(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hay_modulos_que_revisar():
    assert MODULOS, f"no se encontraron modulos en {PAQUETE}"


@pytest.mark.parametrize("modulo", MODULOS, ids=lambda p: p.name)
def test_h1_no_escribe_en_os_environ(modulo):
    fuente = _fuente(modulo)
    assert "os.environ[" not in fuente
    assert "os.environ.setdefault" not in fuente
    assert "os.putenv" not in fuente


@pytest.mark.parametrize("modulo", MODULOS, ids=lambda p: p.name)
def test_h2_no_llama_load_dotenv(modulo):
    assert "load_dotenv" not in _fuente(modulo)


@pytest.mark.parametrize("modulo", MODULOS, ids=lambda p: p.name)
def test_h3_no_hay_basicconfig_en_ningun_modulo(modulo):
    """
    G4/H3: `basicConfig()` toca el root logger del host. Esta libreria configura solo
    su propio logger, asi que el token no debe aparecer en ningun modulo, ni en el CLI.
    """
    assert "basicConfig" not in _fuente(modulo)


def _nombre_llamado(call: ast.Call) -> str:
    """Nombre punteado de lo que se llama: `warnings.filterwarnings`, `emit`, etc."""
    partes = []
    nodo: ast.expr = call.func
    while isinstance(nodo, ast.Attribute):
        partes.append(nodo.attr)
        nodo = nodo.value
    if isinstance(nodo, ast.Name):
        partes.append(nodo.id)
    return ".".join(reversed(partes))


def _filtros_de_warnings_permanentes(fuente: str) -> list[str]:
    """
    Llamadas que mutan los filtros de warnings **sin** restaurarlos al salir.

    Lo que G5 prohibe es dejar el estado tocado, no usar la API: un
    `warnings.filterwarnings(...)` dentro de un `with warnings.catch_warnings():` se
    revierte al salir del bloque, asi que no le cambia la configuracion al host.
    """
    arbol = ast.parse(fuente)

    protegidos: set[int] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.With) and any(
            isinstance(item.context_expr, ast.Call)
            and _nombre_llamado(item.context_expr).endswith("catch_warnings")
            for item in nodo.items
        ):
            protegidos.update(id(hijo) for hijo in ast.walk(nodo))

    permanentes = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call) or id(nodo) in protegidos:
            continue
        nombre = _nombre_llamado(nodo)
        if nombre.endswith(("filterwarnings", "simplefilter", "resetwarnings")):
            permanentes.append(f"{nombre}() en la linea {nodo.lineno}")
    return permanentes


@pytest.mark.parametrize("modulo", MODULOS, ids=lambda p: p.name)
def test_g5_no_deja_filterwarnings_permanente(modulo):
    """
    G5: la libreria no le cambia la configuracion de warnings al host.

    Se revisa por AST y no por substring, porque `extractor.py` si silencia un warning
    concreto -el de pandas sobre conexiones DBAPI2- y lo hace dentro de
    `catch_warnings()`, que restaura el estado al salir. Prohibir el token dejaria fuera
    el uso correcto junto con el incorrecto.
    """
    permanentes = _filtros_de_warnings_permanentes(_fuente(modulo))
    assert not permanentes, (
        f"{modulo.name} muta los filtros de warnings sin restaurarlos: "
        + ", ".join(permanentes)
        + ". Envuelvelo en `with warnings.catch_warnings():`."
    )


def test_h4_importar_no_toca_el_root_logger():
    import importlib

    root = logging.getLogger()
    antes = (root.level, list(root.handlers))
    for nombre in ("redshift_extractor", "redshift_extractor.extractor", "redshift_extractor.cli"):
        importlib.import_module(nombre)
    assert (root.level, list(root.handlers)) == antes


def test_g3_el_logger_propio_trae_nullhandler():
    from redshift_extractor.logging import get_logger

    logger = get_logger()
    assert logger.name == "redshift_extractor"
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_h8_el_logger_de_ssh_no_propaga():
    from redshift_extractor.logging import get_ssh_logger

    logger = get_ssh_logger()
    assert logger.propagate is False
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)


def test_c3_log_level_pide_prefijo_propio(monkeypatch):
    """
    C3: un `LOG_LEVEL` suelto es del host o de otra libreria. Solo se consume el del
    env propio y el override con prefijo.
    """
    from redshift_extractor.logging import resolve_level

    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    monkeypatch.delenv("REDSHIFT_EXTRACTOR_LOG_LEVEL", raising=False)
    assert resolve_level() == "INFO"

    assert resolve_level(env_values={"LOG_LEVEL": "warning"}) == "WARNING"

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_LOG_LEVEL", "debug")
    assert resolve_level(env_values={"LOG_LEVEL": "warning"}) == "DEBUG"
    assert resolve_level("error", env_values={"LOG_LEVEL": "warning"}) == "ERROR"


def test_h5_puerto_local_efimero_por_default(write_env, minimal_env):
    from redshift_extractor.config import load_config

    write_env(minimal_env.replace("SSH_LOCAL_PORT=0", ""))
    ssh, _rs_map = load_config()
    assert ssh.local_port == 0


def test_c1_no_lee_el_env_del_host(tmp_path, monkeypatch, write_env, minimal_env):
    """C1: un `.env` del proyecto host no se toca, ni siquiera si esta en el cwd."""
    env_propio = write_env(minimal_env)
    (tmp_path / ".env").write_text("SSH_HOST=host-del-proyecto\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    from redshift_extractor.config import load_config

    ssh, _rs_map = load_config()
    assert ssh.host == "bastion.example.test"
    assert env_propio.exists()


def test_c2_cargar_config_no_escribe_en_os_environ(write_env, minimal_env):
    import os

    write_env(minimal_env)
    antes = dict(os.environ)

    from redshift_extractor.config import load_config

    load_config()
    assert dict(os.environ) == antes
