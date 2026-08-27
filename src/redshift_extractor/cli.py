from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd
import typer

from redshift_extractor.errors import ConfigError, TunnelError
from redshift_extractor.extractor import extract_sql, list_aliases, ping
from redshift_extractor.io import save_dataframe
from redshift_extractor.logging import configure_logging

app = typer.Typer(add_completion=False)

DEFAULT_LIMIT = 10

#: Codigos de salida del contrato de la CLI (F4). `negocio=1` se conserva porque es
#: lo que esta libreria ha devuelto siempre y hay scripts que lo revisan; config y
#: tunel se separan para poder distinguirlos sin leer el texto del error.
EXIT_OK = 0
EXIT_BUSINESS = 1
EXIT_CONFIG = 2
EXIT_TUNNEL = 3

CONNECTION_ERROR_HINTS = (
    "could not establish connection",
    "connection refused",
    "connection reset",
    "connection timed out",
    "server closed the connection",
    "ssh",
    "tunnel",
    "timeout",
    "timed out",
    "operationalerror",
)

ALIAS_OPTION = typer.Option(None, "--alias", help="Alias de cluster (default: DEFAULT_ALIAS).")


def console_level(debug: bool = False) -> str:
    """
    Nivel del logger de consola del CLI.

    WARNING a proposito: los eventos INFO ya le llegan al usuario por `on_event` —que
    es como ve el progreso del tunel— asi que mandarlos tambien al log los imprimiria
    dos veces. Con `--debug` bajan los dos.
    """
    return "DEBUG" if debug else "WARNING"


def guarded(action: Callable[[], None]) -> None:
    """Traduce excepciones a codigos de salida: 1 negocio, 2 configuracion, 3 tunel."""
    try:
        action()
    except typer.Exit:
        raise
    except ConfigError as exc:
        typer.echo(f"ERROR DE CONFIGURACION - {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIG)
    except TunnelError as exc:
        typer.echo(f"ERROR DE TUNEL - {exc}", err=True)
        raise typer.Exit(code=EXIT_TUNNEL)
    except Exception as exc:
        typer.echo(f"ERROR - {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=EXIT_BUSINESS)


def printer(debug: bool = False) -> Callable[[Dict[str, Any]], None]:
    def _print(event: Dict[str, Any]) -> None:
        if event["level"] == "DEBUG" and not debug:
            return
        extras = {
            key: value
            for key, value in event.items()
            if key not in ("ts", "level", "event", "message")
        }
        typer.echo(
            f'{event["ts"]} [{event["level"]}] {event["event"]}: {event["message"]} | {extras}',
            err=True,
        )

    return _print


def read_sql(sql_file: Path) -> str:
    if not sql_file.exists():
        raise FileNotFoundError(f"No existe el archivo: {sql_file}")
    if not sql_file.is_file():
        raise ValueError(f"No es un archivo: {sql_file}")
    return sql_file.read_text(encoding="utf-8")


def strip_trailing_semicolons(sql: str) -> str:
    cleaned = sql.strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def apply_limit(sql: str, limit: Optional[int]) -> str:
    cleaned = strip_trailing_semicolons(sql)
    if not cleaned:
        raise ValueError("El archivo SQL esta vacio.")

    if limit is None:
        return cleaned
    if limit <= 0:
        raise ValueError("--limit debe ser mayor a 0. Usa --full si no quieres limite.")

    first_word = cleaned.lstrip().split(maxsplit=1)[0].lower()
    if first_word not in {"select", "with"}:
        raise ValueError(
            "El modo LIMIT solo funciona con SELECT/WITH. Usa --full para ejecutar este SQL."
        )

    return f"SELECT *\nFROM (\n{cleaned}\n) AS query_limitada\nLIMIT {limit}"


def is_connection_error(error: Exception) -> bool:
    message = f"{type(error).__name__}: {error}".lower()
    return any(hint in message for hint in CONNECTION_ERROR_HINTS)


def execute_with_retries(
    connection: Optional[str], sql: str, retries: int, retry_wait: float
) -> pd.DataFrame:
    if retries <= 0:
        raise ValueError("--retries debe ser mayor a 0.")

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                typer.echo(f"Reintento {attempt}/{retries}...")
            return extract_sql(sql, alias=connection)
        except Exception as error:
            last_error = error
            if not is_connection_error(error):
                raise
            if attempt == retries:
                break
            typer.echo(f"Fallo de conexion. Esperando {retry_wait:.1f}s antes de reintentar...")
            time.sleep(retry_wait)

    assert last_error is not None
    raise last_error


def print_result(df: pd.DataFrame, elapsed_seconds: float) -> None:
    typer.echo(f"OK - Query ejecutado en {elapsed_seconds:.1f}s")
    rows, cols = df.shape
    typer.echo(f"Filas: {rows:,}")
    typer.echo(f"Columnas: {cols:,}")
    typer.echo("")
    typer.echo(df.head(DEFAULT_LIMIT).to_string(index=False))


@app.command()
def ls() -> None:
    """
    Lista aliases disponibles.
    """

    def action() -> None:
        configure_logging(console_level())
        for a in list_aliases():
            typer.echo(a)

    guarded(action)


@app.command("ping")
def ping_command(
    alias: Optional[str] = ALIAS_OPTION,
    debug: bool = typer.Option(False, "--debug", help="Muestra eventos DEBUG."),
) -> None:
    """
    Verifica la conexion de punta a punta: tunel, cluster y credenciales.
    """

    def action() -> None:
        configure_logging(console_level(debug))
        result = ping(alias, on_event=printer(debug))
        for key, value in result.items():
            typer.echo(f"{key}: {value}")

    guarded(action)


@app.command("fingerprint")
def fingerprint_command(
    alias: Optional[str] = ALIAS_OPTION,
) -> None:
    """
    Muestra el fingerprint de la host key que presenta el bastion.
    """

    def action() -> None:
        from redshift_extractor import config as config_mod
        from redshift_extractor.tunnel import fetch_remote_host_key, fingerprint

        configure_logging(console_level())
        _app, ssh, _resolved, _rs = config_mod.resolve(alias)
        key = fetch_remote_host_key(ssh)
        typer.echo(f"host: {ssh.host}:{ssh.port}")
        typer.echo(f"tipo: {key.get_name()}")
        typer.echo(f"fingerprint: {fingerprint(key)}")
        typer.echo("")
        typer.echo(
            "Verificalo con quien administra el bastion y pegalo en el env propio:\n"
            f"  SSH_HOST_FINGERPRINT={fingerprint(key)}"
        )

    guarded(action)


@app.command()
def run(
    query: str = typer.Option(..., "--query", help="SQL a ejecutar (entre comillas)"),
    alias: Optional[str] = ALIAS_OPTION,
    out: str = typer.Option("./output/result.parquet", help="Ruta de salida"),
    fmt: str = typer.Option("parquet", help="csv|parquet"),
) -> None:
    """
    Ejecuta un query y guarda el resultado a archivo.
    """

    def action() -> None:
        configure_logging(console_level())
        df = extract_sql(query, alias=alias)
        out_path = save_dataframe(df, out, fmt=fmt)  # type: ignore[arg-type]
        typer.echo(f"OK -> {out_path}")

    guarded(action)


@app.command("run-file")
def run_file(
    sql_file: Path = typer.Argument(..., help="Ruta del archivo .sql a ejecutar."),
    alias: Optional[str] = ALIAS_OPTION,
    limit: int = typer.Option(
        DEFAULT_LIMIT, help=f"Limite de filas para prueba rapida. Default: {DEFAULT_LIMIT}"
    ),
    full: bool = typer.Option(
        False, "--full", help="Ejecuta el query completo, sin envolverlo con LIMIT."
    ),
    retries: int = typer.Option(3, help="Intentos maximos si falla la conexion. Default: 3"),
    retry_wait: float = typer.Option(
        5.0, help="Segundos de espera entre reintentos de conexion. Default: 5"
    ),
    output: Optional[Path] = typer.Option(None, help="Opcional: guarda el resultado en CSV."),
    print_sql: bool = typer.Option(
        False, "--print-sql", help="Imprime el SQL final que se va a ejecutar."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Solo arma/imprime el SQL final; no lo ejecuta."
    ),
) -> None:
    """
    Ejecuta un archivo .sql. Por defecto aplica LIMIT 10 (usa --full para el query completo).
    """

    def action() -> None:
        configure_logging(console_level())
        raw_sql = read_sql(sql_file)
        effective_limit = None if full else limit
        final_sql = apply_limit(raw_sql, effective_limit)

        mode = "FULL" if full else f"LIMIT {limit}"
        typer.echo(f"Conexion: {alias or 'DEFAULT_ALIAS'}")
        typer.echo(f"Archivo: {sql_file}")
        typer.echo(f"Modo: {mode}")

        if print_sql:
            typer.echo("")
            typer.echo(final_sql)
            typer.echo("")

        if dry_run:
            typer.echo("DRY RUN - No se ejecuto el query.")
            return

        started_at = time.perf_counter()
        df = execute_with_retries(
            connection=alias,
            sql=final_sql,
            retries=retries,
            retry_wait=retry_wait,
        )
        elapsed_seconds = time.perf_counter() - started_at

        print_result(df, elapsed_seconds)

        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(output, index=False)
            typer.echo(f"\nCSV guardado en: {output}")

    guarded(action)
