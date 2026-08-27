from __future__ import annotations

import os
import time
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
import paramiko  # type: ignore[import-untyped]
import psycopg2
from sshtunnel import BaseSSHTunnelForwarderError

from redshift_extractor import config as _config
from redshift_extractor.errors import (
    QueryError,
    RedshiftExtractorError,
    TunnelError,
)
from redshift_extractor.events import OnEvent, StatusEvent, emit
from redshift_extractor.io import write_parquet
from redshift_extractor.tunnel import open_tunnel
from redshift_extractor.types import RedshiftConfig

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
EventType = Literal[
    "CONFIG_LOADED",
    "ALIAS_RESOLVED",
    "TUNNEL_START",
    "TUNNEL_READY",
    "TUNNEL_CLOSED",
    "DB_CONNECT_START",
    "DB_CONNECTED",
    "QUERY_START",
    "QUERY_OK",
    "FILE_SAVED",
    "CONNECTION_CLOSED",
    "DONE",
    "ERROR",
]

# `OnEvent` y `StatusEvent` vivian aqui antes de que existiera events.py (G1). Se
# re-exportan para no romper a quien haga
# `from redshift_extractor.extractor import OnEvent`.
_emit = emit

_CONNECT_TIMEOUT_S = 15


def _read_sql_file(sql_file: str | Path) -> str:
    path = Path(sql_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")
    if not path.is_file():
        raise ValueError(f"No es un archivo: {path}")
    return path.read_text(encoding="utf-8")


def list_available_aliases(redshift_map: Dict[str, RedshiftConfig]) -> List[str]:
    return sorted(redshift_map.keys())


def list_aliases(*, on_event: Optional[OnEvent] = None) -> List[str]:
    """
    Lista los aliases configurados (normalizados a lowercase). No abre el tunel.
    """
    _app, _ssh, rs_map = _config.load_full_config(on_event=on_event)
    return list_available_aliases(rs_map)


def _connect(rs: RedshiftConfig, local_port: int) -> Any:
    """Conecta a Redshift por el puerto local del tunel, envolviendo psycopg2 (F2)."""
    try:
        return psycopg2.connect(
            # 127.0.0.1 y no "localhost": el tunel escucha solo en IPv4, y "localhost"
            # resuelve primero a ::1 en Windows, asi que psycopg2 gasta un intento
            # rechazado antes de dar con el puerto bueno.
            host="127.0.0.1",
            port=local_port,
            dbname=rs.dbname,
            user=rs.user,
            password=rs.password,
            connect_timeout=_CONNECT_TIMEOUT_S,
        )
    except psycopg2.Error as exc:
        raise _query_error(exc) from exc


def _query_error(exc: psycopg2.Error) -> QueryError:
    msg = f"{exc}"
    pgcode = getattr(exc, "pgcode", None)
    pgerror = getattr(exc, "pgerror", None)
    full = f"Error psycopg2: {msg}"
    if pgcode:
        full += f" | pgcode={pgcode}"
    if pgerror:
        full += f" | pgerror={pgerror}"
    return QueryError(full)


def extract_sql(
    query: Optional[str] = None,
    *,
    alias: Optional[str] = None,
    query_file: Optional[str] = None,
    on_event: Optional[OnEvent] = None,
    save_dir: Optional[str] = None,
    base_name: Optional[str] = None,
    save_csv: bool = False,
    save_parquet: bool = False,
    csv_index: bool = False,
    csv_encoding: str = "utf-8",
    parquet_index: bool = False,
) -> pd.DataFrame:
    """
    Ejecuta un SQL en el alias indicado y devuelve un DataFrame.

    Forma canonica (E2, E4):

        extract_sql("select 1")                      # alias = DEFAULT_ALIAS
        extract_sql("select 1", alias="prod")
        extract_sql(query_file="q.sql", alias="prod")

    Parametros:
      - query: SQL a ejecutar (prioridad sobre query_file). Posicional opcional.
      - alias: alias del cluster. Keyword-only; si se omite, se usa `DEFAULT_ALIAS`
        del env propio.
      - query_file: ruta a un archivo .sql (usado si query es None).

    Persistencia opcional:
      - save_dir: carpeta destino (si None, no guarda nada)
      - base_name: nombre base (sin extension). Si None, genera uno.
      - save_csv / save_parquet: formatos a guardar. Parquet necesita el extra
        `parquet` (pip install "redshift-extractor[parquet]").

    Las formas de 0.1.0 —el alias como primer posicional y `db=` en vez de `alias=`—
    se retiraron en 0.3.0. Un `extract_sql("prod", "select 1")` ahora es un TypeError.
    """
    started = dt.now()

    # Preferencia: query > query_file
    if query is not None:
        final_query = query
    elif query_file is not None:
        final_query = _read_sql_file(query_file)
    else:
        raise ValueError("Debes proporcionar 'query' o 'query_file'.")

    alias_in = alias
    app, ssh, resolved, rs = _config.resolve(alias, on_event=on_event)
    emit(
        on_event,
        level="INFO",
        event="ALIAS_RESOLVED",
        message="Resolving database alias.",
        alias_input=alias_in,
        alias=resolved,
    )

    emit(
        on_event,
        level="INFO",
        event="TUNNEL_START",
        message="Establishing SSH tunnel.",
        alias=resolved,
        redshift_host=rs.host,
        redshift_port=rs.port,
        redshift_dbname=rs.dbname,
    )

    # Normaliza logica de guardado
    want_save = bool(save_dir) and (save_csv or save_parquet)
    csv_path = pq_path = None
    if want_save:
        assert save_dir is not None
        out_dir = Path(save_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        bn = base_name or f"{resolved}_{rs.dbname}_{dt.now().strftime('%Y%m%d_%H%M%S')}"
        csv_path = out_dir / f"{bn}.csv"
        pq_path = out_dir / f"{bn}.parquet"

        emit(
            on_event,
            level="INFO",
            event="QUERY_START",
            message="Output persistence enabled.",
            save_dir=str(out_dir),
            base_name=bn,
            save_csv=save_csv,
            save_parquet=save_parquet,
        )

    try:
        with open_tunnel(ssh, rs, on_event=on_event) as tunnel:
            conn = None
            try:
                emit(
                    on_event,
                    level="INFO",
                    event="DB_CONNECT_START",
                    message="Connecting to Redshift.",
                    alias=resolved,
                    dbname=rs.dbname,
                )

                conn = _connect(rs, tunnel.local_bind_port)

                emit(
                    on_event,
                    level="INFO",
                    event="DB_CONNECTED",
                    message="Connected to Redshift.",
                    alias=resolved,
                    dbname=rs.dbname,
                )

                emit(
                    on_event,
                    level="INFO",
                    event="QUERY_START",
                    message="Executing query.",
                    alias=resolved,
                )

                df = pd.read_sql(final_query, conn)

                emit(
                    on_event,
                    level="INFO",
                    event="QUERY_OK",
                    message="Query executed successfully.",
                    rows=int(len(df)),
                    cols=int(len(df.columns)),
                )

                if want_save:
                    if save_csv:
                        assert csv_path is not None
                        df.to_csv(csv_path, index=csv_index, encoding=csv_encoding)
                        emit(
                            on_event,
                            level="INFO",
                            event="FILE_SAVED",
                            message="CSV saved.",
                            path=str(csv_path),
                            rows=int(len(df)),
                            bytes=int(os.path.getsize(csv_path)),
                        )

                    if save_parquet:
                        assert pq_path is not None
                        write_parquet(df, pq_path, index=parquet_index)
                        emit(
                            on_event,
                            level="INFO",
                            event="FILE_SAVED",
                            message="Parquet saved.",
                            path=str(pq_path),
                            rows=int(len(df)),
                            bytes=int(os.path.getsize(pq_path)),
                        )

                return df

            finally:
                if conn is not None:
                    conn.close()
                    emit(
                        on_event,
                        level="DEBUG",
                        event="CONNECTION_CLOSED",
                        message="Connection closed.",
                        alias=resolved,
                    )

    except RedshiftExtractorError as e:
        # Ya viene tipado y con mensaje accionable: no se re-envuelve.
        emit(on_event, level="ERROR", event="ERROR", message=str(e), error_type=type(e).__name__)
        raise

    except paramiko.ssh_exception.AuthenticationException as e:
        emit(on_event, level="ERROR", event="ERROR", message="SSH authentication failed.", error=str(e))
        raise TunnelError(
            f"Error autenticacion SSH: {e}. Revisa SSH_PKEY_PATH y permisos "
            "(chmod 400 en Linux/macOS)."
        ) from e

    except BaseSSHTunnelForwarderError as e:
        emit(on_event, level="ERROR", event="ERROR", message="SSH tunnel failed.", error=str(e))
        raise TunnelError(
            f"Error al establecer tunel SSH: {e}. Revisa SSH_HOST/SSH_PORT y conectividad "
            "al bastion."
        ) from e

    except psycopg2.Error as e:
        error = _query_error(e)
        emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="Database error.",
            error=str(e),
            pgcode=getattr(e, "pgcode", None),
            pgerror=getattr(e, "pgerror", None),
        )
        raise error from e

    except Exception as e:
        emit(on_event, level="ERROR", event="ERROR", message="Unexpected error.", error=str(e))
        raise RedshiftExtractorError(f"Error inesperado al extraer: {e}") from e

    finally:
        emit(
            on_event,
            level="INFO",
            event="DONE",
            message="Extraction finished.",
            alias=resolved,
            elapsed_s=round((dt.now() - started).total_seconds(), 3),
        )


def ping(
    alias: Optional[str] = None,
    *,
    on_event: Optional[OnEvent] = None,
) -> Dict[str, Any]:
    """
    Verifica la conexion de punta a punta y reporta a donde quedo conectada de verdad.

    `database` y `user` salen del servidor, no de la config: es la forma de detectar un
    tunel que quedo apuntando al cluster equivocado. No expone credenciales (E6).

    Devuelve la clave `"alias"`; nunca `"db"`, a proposito (contrato del renombre).
    """
    _app, ssh, resolved, rs = _config.resolve(alias, on_event=on_event)
    started = time.perf_counter()

    with open_tunnel(ssh, rs, on_event=on_event) as tunnel:
        tunnel_port = int(tunnel.local_bind_port)
        conn = _connect(rs, tunnel_port)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select version(), current_database(), current_user"
                )
                row = cur.fetchone()
        except psycopg2.Error as exc:
            raise _query_error(exc) from exc
        finally:
            conn.close()

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    result = {
        "ok": True,
        "alias": resolved,
        "server_version": row[0],
        "database": row[1],
        "user": row[2],
        "redshift_host": rs.host,
        "redshift_port": rs.port,
        "tunnel_port": tunnel_port,
        "latency_ms": latency_ms,
    }
    emit(
        on_event,
        level="INFO",
        event="QUERY_OK",
        message=f"ping ok a {row[1]} como {row[2]}.",
        alias=resolved,
        local_port=tunnel_port,
        elapsed_s=round(latency_ms / 1000, 3),
    )
    return result


__all__ = [
    "EventType",
    "Level",
    "OnEvent",
    "StatusEvent",
    "extract_sql",
    "list_aliases",
    "list_available_aliases",
    "ping",
]
