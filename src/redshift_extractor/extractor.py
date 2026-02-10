from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime as dt
from typing import Any, Callable, Dict, List, Literal, Optional

import pandas as pd
import psycopg2
import paramiko
from sshtunnel import BaseSSHTunnelForwarderError

from redshift_extractor.config import load_config
from redshift_extractor.tunnel import open_tunnel
from redshift_extractor.types import RedshiftConfig

from pathlib import Path
import os

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
EventType = Literal[
    "CONFIG_LOADED",
    "ALIAS_RESOLVED",
    "TUNNEL_START",
    "TUNNEL_READY",
    "DB_CONNECT_START",
    "DB_CONNECTED",
    "QUERY_START",
    "QUERY_OK",
    "CONNECTION_CLOSED",
    "DONE",
    "ERROR",
]

StatusEvent = Dict[str, Any]
OnEvent = Callable[[StatusEvent], None]


def _noop_event(_: StatusEvent) -> None:
    return


def _emit(
    on_event: Optional[OnEvent],
    *,
    level: Level,
    event: EventType,
    message: str,
    **fields: Any,
) -> None:
    if on_event is None:
        return
    payload: StatusEvent = {
        "ts": dt.now().isoformat(timespec="seconds"),
        "level": level,
        "event": event,
        "message": message,
        **fields,
    }
    on_event(payload)


def list_available_databases(redshift_map: Dict[str, RedshiftConfig]) -> List[str]:
    return sorted(redshift_map.keys())


def list_databases(*, on_event: Optional[OnEvent] = None) -> List[str]:
    """
    Lista aliases disponibles (normalizados a lowercase).
    """
    ssh, rs_map = load_config()
    _emit(
        on_event,
        level="INFO",
        event="CONFIG_LOADED",
        message="Config loaded.",
        aliases=len(rs_map),
        ssh_host=ssh.host,
        ssh_port=ssh.port,
    )
    return list_available_databases(rs_map)


def extract_sql(
    db: str,
    query: str,
    *,
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
    Ejecuta un SQL en el alias `db` y devuelve un DataFrame.

    Persistencia opcional:
      - save_dir: carpeta destino (si None, no guarda nada)
      - base_name: nombre base (sin extensión). Si None, genera uno.
      - save_csv: guardar CSV
      - save_parquet: guardar Parquet

    Notas:
      - Para Parquet, pandas requiere pyarrow o fastparquet.
        Recomendado: pyarrow>=18 (ya lo traías).
      - Si save_dir existe, se crea (mkdir -p).
    """
    started = dt.now()
    db_in = db
    db = db.lower()

    _emit(
        on_event,
        level="INFO",
        event="ALIAS_RESOLVED",
        message="Resolving database alias.",
        db_input=db_in,
        db=db,
    )

    ssh, rs_map = load_config()
    _emit(
        on_event,
        level="INFO",
        event="CONFIG_LOADED",
        message="Config loaded.",
        aliases=len(rs_map),
        ssh_host=ssh.host,
        ssh_port=ssh.port,
    )

    if db not in rs_map:
        available = sorted(rs_map.keys())
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="DB alias not found.",
            db=db,
            available=available,
        )
        raise ValueError(f"DB alias '{db}' no existe. Disponibles: {', '.join(available)}")

    rs = rs_map[db]
    _emit(
        on_event,
        level="INFO",
        event="TUNNEL_START",
        message="Establishing SSH tunnel.",
        db=db,
        redshift_host=rs.host,
        redshift_port=rs.port,
        redshift_dbname=rs.dbname,
    )

    # Normaliza lógica de guardado
    want_save = bool(save_dir) and (save_csv or save_parquet)
    if want_save:
        out_dir = Path(save_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        # nombre base por defecto
        if base_name:
            bn = base_name
        else:
            ts = dt.now().strftime("%Y%m%d_%H%M%S")
            bn = f"{db}_{rs.dbname}_{ts}"
        csv_path = out_dir / f"{bn}.csv"
        pq_path = out_dir / f"{bn}.parquet"

        _emit(
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
        with open_tunnel(ssh, rs) as tunnel:
            _emit(
                on_event,
                level="INFO",
                event="TUNNEL_READY",
                message="SSH tunnel ready.",
                local_port=tunnel.local_bind_port,
            )

            conn = None
            try:
                _emit(
                    on_event,
                    level="INFO",
                    event="DB_CONNECT_START",
                    message="Connecting to Redshift.",
                    db=db,
                    dbname=rs.dbname,
                )

                conn = psycopg2.connect(
                    host="localhost",
                    port=tunnel.local_bind_port,
                    dbname=rs.dbname,
                    user=rs.user,
                    password=rs.password,
                    connect_timeout=15,
                )

                _emit(
                    on_event,
                    level="INFO",
                    event="DB_CONNECTED",
                    message="Connected to Redshift.",
                    db=db,
                    dbname=rs.dbname,
                )

                _emit(
                    on_event,
                    level="INFO",
                    event="QUERY_START",
                    message="Executing query.",
                    db=db,
                )

                df = pd.read_sql(query, conn)

                _emit(
                    on_event,
                    level="INFO",
                    event="QUERY_OK",
                    message="Query executed successfully.",
                    rows=int(len(df)),
                    cols=int(len(df.columns)),
                )

                # -------------------------
                # Guardado opcional
                # -------------------------
                if want_save:
                    if save_csv:
                        _emit(
                            on_event,
                            level="INFO",
                            event="QUERY_START",
                            message="Saving CSV output.",
                            path=str(csv_path),
                            rows=int(len(df)),
                        )
                        df.to_csv(csv_path, index=csv_index, encoding=csv_encoding)
                        _emit(
                            on_event,
                            level="INFO",
                            event="QUERY_OK",
                            message="CSV saved.",
                            path=str(csv_path),
                            bytes=int(os.path.getsize(csv_path)),
                        )

                    if save_parquet:
                        _emit(
                            on_event,
                            level="INFO",
                            event="QUERY_START",
                            message="Saving Parquet output.",
                            path=str(pq_path),
                            rows=int(len(df)),
                        )
                        df.to_parquet(pq_path, index=parquet_index)
                        _emit(
                            on_event,
                            level="INFO",
                            event="QUERY_OK",
                            message="Parquet saved.",
                            path=str(pq_path),
                            bytes=int(os.path.getsize(pq_path)),
                        )

                return df

            finally:
                if conn is not None:
                    conn.close()
                    _emit(
                        on_event,
                        level="DEBUG",
                        event="CONNECTION_CLOSED",
                        message="Connection closed.",
                        db=db,
                    )

    except paramiko.ssh_exception.AuthenticationException as e:
        _emit(on_event, level="ERROR", event="ERROR", message="SSH authentication failed.", error=str(e))
        raise RuntimeError(
            f"Error autenticación SSH: {e}. Revisa SSH_PKEY_PATH y permisos (chmod 400 en Linux/macOS)."
        ) from e

    except BaseSSHTunnelForwarderError as e:
        _emit(on_event, level="ERROR", event="ERROR", message="SSH tunnel failed.", error=str(e))
        raise RuntimeError(
            f"Error al establecer túnel SSH: {e}. Revisa SSH_HOST/SSH_PORT y conectividad al bastion."
        ) from e

    except psycopg2.Error as e:
        msg = f"{e}"
        pgcode = getattr(e, "pgcode", None)
        pgerror = getattr(e, "pgerror", None)
        _emit(
            on_event,
            level="ERROR",
            event="ERROR",
            message="Database error.",
            error=msg,
            pgcode=pgcode,
            pgerror=pgerror,
        )
        full = f"Error psycopg2: {msg}"
        if pgcode:
            full += f" | pgcode={pgcode}"
        if pgerror:
            full += f" | pgerror={pgerror}"
        raise RuntimeError(full) from e

    except Exception as e:
        _emit(on_event, level="ERROR", event="ERROR", message="Unexpected error.", error=str(e))
        raise RuntimeError(f"Error inesperado al extraer: {e}") from e

    finally:
        ended = dt.now()
        _emit(
            on_event,
            level="INFO",
            event="DONE",
            message="Extraction finished.",
            db=db.lower(),
            elapsed_s=round((ended - started).total_seconds(), 3),
        )