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


# -----------------------------------------------------------------------------
# G1 / pendiente F: la secuencia de eventos, no solo el catalogo
# -----------------------------------------------------------------------------
def _extraccion_con_eventos(monkeypatch, tmp_path, collect, **kwargs):
    """
    Corre `extract_sql` de punta a punta contra el tunel de prueba, con la consulta
    simulada.

    `tests/fakepg.py` contesta el handshake del protocolo pero no ejecuta SQL, asi que
    la conexion y el `read_sql` se sustituyen. Todo lo demas —resolucion del alias,
    tunel real con su handshake SSH, cierre— corre de verdad, que es lo que hace falta
    para ver la secuencia completa.
    """
    import pandas as pd

    from redshift_extractor import extractor as extractor_mod

    class _ConexionFalsa:
        def close(self) -> None:
            pass

    monkeypatch.setattr(extractor_mod, "_connect", lambda rs, puerto: _ConexionFalsa())
    monkeypatch.setattr(
        extractor_mod.pd, "read_sql", lambda sql, conn: pd.DataFrame({"a": [1, 2]})
    )
    return extractor_mod.extract_sql("select 1", on_event=collect, **kwargs)


def test_tunnel_start_se_emite_una_sola_vez(tunnel_env, events_log, monkeypatch, tmp_path):
    """
    Habia dos emisores de `TUNNEL_START` para el mismo tunel: `extractor.extract_sql` y
    `tunnel.open_tunnel`. Quien midiera TUNNEL_START -> TUNNEL_READY para sacar latencia
    del tunel arrancaba el cronometro en el evento equivocado.
    """
    collected, collect = events_log
    tunnel_env()

    _extraccion_con_eventos(monkeypatch, tmp_path, collect)

    eventos = [e["event"] for e in collected]
    assert eventos.count("TUNNEL_START") == 1, eventos
    assert eventos.count("TUNNEL_READY") == 1, eventos


def test_el_tunnel_start_que_queda_trae_el_puerto_y_el_alias(
    tunnel_env, events_log, monkeypatch, tmp_path
):
    """Se conservo el de `tunnel.py`, que es el unico que conoce el puerto local."""
    collected, collect = events_log
    tunnel_env()

    _extraccion_con_eventos(monkeypatch, tmp_path, collect)

    evento = next(e for e in collected if e["event"] == "TUNNEL_START")
    assert evento["alias"] == "prod"
    assert evento["redshift_dbname"] == "analytics"
    assert "local_port" in evento
    assert "ssh_host" in evento


def test_query_start_se_emite_una_sola_vez_con_persistencia(
    tunnel_env, events_log, monkeypatch, tmp_path
):
    """
    El aviso de "guardado activado" salia como `QUERY_START`, asi que una extraccion
    con persistencia reportaba dos consultas donde hubo una.
    """
    collected, collect = events_log
    tunnel_env()

    _extraccion_con_eventos(
        monkeypatch,
        tmp_path,
        collect,
        save_dir=str(tmp_path / "salida"),
        base_name="prueba",
        save_csv=True,
    )

    eventos = [e["event"] for e in collected]
    assert eventos.count("QUERY_START") == 1, eventos
    assert eventos.count("SAVE_CONFIGURED") == 1, eventos
    assert eventos.count("QUERY_OK") == 1, eventos
    assert eventos.count("FILE_SAVED") == 1, eventos


def test_save_configured_esta_en_el_catalogo_y_trae_su_alias(
    tunnel_env, events_log, monkeypatch, tmp_path
):
    collected, collect = events_log
    tunnel_env()

    _extraccion_con_eventos(
        monkeypatch,
        tmp_path,
        collect,
        save_dir=str(tmp_path / "salida"),
        base_name="prueba",
        save_csv=True,
    )

    evento = next(e for e in collected if e["event"] == "SAVE_CONFIGURED")
    assert "SAVE_CONFIGURED" in events_mod.KNOWN_EVENTS
    assert evento["alias"] == "prod"
    assert evento["save_csv"] is True


def test_todo_evento_emitido_esta_en_el_catalogo(
    tunnel_env, events_log, monkeypatch, tmp_path
):
    collected, collect = events_log
    tunnel_env()

    _extraccion_con_eventos(
        monkeypatch,
        tmp_path,
        collect,
        save_dir=str(tmp_path / "salida"),
        base_name="prueba",
        save_csv=True,
    )

    desconocidos = {e["event"] for e in collected} - events_mod.KNOWN_EVENTS
    assert not desconocidos, f"eventos fuera del catalogo: {desconocidos}"


# -----------------------------------------------------------------------------
# Pendiente H: el UserWarning de pandas no se filtra a consola
# -----------------------------------------------------------------------------
def test_read_sql_no_deja_salir_el_warning_de_sqlalchemy(recwarn):
    """
    `pd.read_sql` sobre una conexion DBAPI2 avisa "pandas only supports SQLAlchemy
    connectable". No indica nada malo -la ruta DBAPI2 es la que esta libreria eligio a
    proposito, para no arrastrar SQLAlchemy- pero ensuciaba la salida del host en cada
    extraccion, por CLI y por API.
    """
    from redshift_extractor import extractor as extractor_mod

    class _ConexionDBAPI2:
        """Suficiente para que pandas la tome por DBAPI2 y dispare el aviso."""

        def cursor(self):
            raise AssertionError("no se llega aca: el warning sale antes")

    with pytest.raises(Exception):
        extractor_mod._read_sql_sin_el_warning_de_sqlalchemy("select 1", _ConexionDBAPI2())

    sqlalchemy = [w for w in recwarn if "SQLAlchemy connectable" in str(w.message)]
    assert not sqlalchemy, f"el warning se filtro: {[str(w.message) for w in sqlalchemy]}"


def test_el_filtro_de_warnings_no_toca_la_configuracion_del_host():
    """
    Se usa `catch_warnings`, que restaura el estado al salir. Un filtro global tocaria
    la configuracion de warnings del host, que es lo que C3 prohibe para el logging y
    aplica igual aqui.
    """
    import warnings as warnings_mod

    from redshift_extractor import extractor as extractor_mod

    class _ConexionDBAPI2:
        def cursor(self):
            raise AssertionError("no se llega aca")

    antes = list(warnings_mod.filters)
    with pytest.raises(Exception):
        extractor_mod._read_sql_sin_el_warning_de_sqlalchemy("select 1", _ConexionDBAPI2())

    assert list(warnings_mod.filters) == antes
