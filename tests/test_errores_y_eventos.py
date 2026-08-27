"""Jerarquia de errores (F1, F2, F3) y modulo de eventos (G1)."""

from __future__ import annotations

import logging

import pytest

from redshift_extractor import events as events_mod
from redshift_extractor.errors import (
    ConfigError,
    EnvFileNotFoundError,
    QueryError,
    RedshiftExtractorError,
    TunnelAuthError,
    TunnelBindError,
    TunnelError,
    TunnelHostKeyError,
    TunnelNetworkError,
)


# -----------------------------------------------------------------------------
# F1: jerarquia
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "clase",
    [
        ConfigError,
        EnvFileNotFoundError,
        QueryError,
        TunnelError,
        TunnelAuthError,
        TunnelBindError,
        TunnelHostKeyError,
        TunnelNetworkError,
    ],
)
def test_todo_error_cuelga_de_la_raiz(clase):
    assert issubclass(clase, RedshiftExtractorError)


def test_la_raiz_sigue_siendo_atrapable_como_runtime_error():
    """
    E8: antes de que existiera errors.py todo salia como RuntimeError o ValueError, y
    los hosts tienen `except` sobre esos tipos. Estrechar la jerarquia los romperia
    en silencio.
    """
    with pytest.raises(RuntimeError):
        raise RedshiftExtractorError("x")
    with pytest.raises(ValueError):
        raise ConfigError("x")
    with pytest.raises(RuntimeError):
        raise ConfigError("x")
    with pytest.raises(FileNotFoundError):
        raise EnvFileNotFoundError("x")


def test_config_error_de_alias_inexistente_dice_los_disponibles(write_env, minimal_env):
    """F3: el mensaje dice que hacer, no solo que fallo."""
    from redshift_extractor import config as config_mod

    write_env(minimal_env)
    with pytest.raises(ConfigError) as excinfo:
        config_mod.resolve("no-existe")
    mensaje = str(excinfo.value)
    assert "dev" in mensaje and "prod" in mensaje


# -----------------------------------------------------------------------------
# G1: eventos
# -----------------------------------------------------------------------------
def test_emit_construye_el_payload_completo():
    recibidos = []
    events_mod.emit(
        recibidos.append,
        level="INFO",
        event="QUERY_OK",
        message="listo",
        rows=3,
    )
    assert len(recibidos) == 1
    evento = recibidos[0]
    assert set(evento) == {"ts", "level", "event", "message", "rows"}
    assert evento["event"] == "QUERY_OK"
    assert evento["rows"] == 3


def test_emit_sin_callback_no_truena():
    events_mod.emit(None, level="INFO", event="DONE", message="sin callback")


def test_un_on_event_roto_no_tumba_la_operacion():
    def explota(_evento):
        raise RuntimeError("el callback del host esta roto")

    events_mod.emit(explota, level="INFO", event="DONE", message="sigue adelante")


def test_los_eventos_emitidos_estan_en_el_catalogo():
    """
    El catalogo conserva los nombres en MAYUSCULAS que esta libreria ya emitia: los
    hosts filtran por esas cadenas exactas (E8).
    """
    from redshift_extractor import extractor as extractor_mod
    from redshift_extractor import tunnel as tunnel_mod

    import inspect
    import re

    emitidos = set()
    for modulo in (extractor_mod, tunnel_mod):
        emitidos.update(re.findall(r'event="([A-Z_]+)"', inspect.getsource(modulo)))

    assert emitidos, "no se encontro ningun evento emitido"
    assert emitidos <= events_mod.KNOWN_EVENTS, sorted(emitidos - events_mod.KNOWN_EVENTS)


def test_register_secret_tacha_el_valor_en_los_mensajes():
    events_mod.register_secret("pa55word-secreto")
    recibidos = []
    events_mod.emit(
        recibidos.append,
        level="ERROR",
        event="ERROR",
        message="fallo con pa55word-secreto adentro",
        detalle="tambien aqui: pa55word-secreto",
    )
    assert "pa55word-secreto" not in recibidos[0]["message"]
    assert "pa55word-secreto" not in recibidos[0]["detalle"]
    assert "***" in recibidos[0]["message"]


class _Captura(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.mensajes: list = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensajes.append(record.getMessage())


def test_emit_manda_al_logger_propio_y_no_al_root():
    """
    G2/G3: el evento va al logger `redshift_extractor`, no al root.

    Se engancha un handler al logger propio en vez de usar `caplog`, que escucha en el
    root: el logger propio tiene `propagate=False` en cuanto alguien llama
    `configure_logging()`, asi que caplog no veria nada.
    """
    from redshift_extractor.logging import get_logger

    logger = get_logger()
    handler = _Captura()
    nivel_previo = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    root = logging.getLogger()
    antes = (root.level, list(root.handlers))
    try:
        events_mod.emit(None, level="INFO", event="DONE", message="al logger propio")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(nivel_previo)

    assert any("al logger propio" in mensaje for mensaje in handler.mensajes)
    assert (root.level, list(root.handlers)) == antes
