from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd
import typer

from redshift_extractor.extractor import extract_sql, list_databases
from redshift_extractor.io import save_dataframe
from redshift_extractor.logging import configure_logging

app = typer.Typer(add_completion=False)

DEFAULT_LIMIT = 10

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
    connection: str, sql: str, retries: int, retry_wait: float
) -> pd.DataFrame:
    if retries <= 0:
        raise ValueError("--retries debe ser mayor a 0.")

    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if attempt > 1:
                typer.echo(f"Reintento {attempt}/{retries}...")
            return extract_sql(db=connection, query=sql)
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
    configure_logging()
    for a in list_databases():
        typer.echo(a)


@app.command()
def run(
    db: str = typer.Option(..., help="Alias de base (ver con: redshift-extractor ls)"),
    query: str = typer.Option(..., help="SQL a ejecutar (entre comillas)"),
    out: str = typer.Option("./output/result.parquet", help="Ruta de salida"),
    fmt: str = typer.Option("parquet", help="csv|parquet"),
) -> None:
    """
    Ejecuta un query y guarda el resultado a archivo.
    """
    configure_logging()
    df = extract_sql(db=db, query=query)
    out_path = save_dataframe(df, out, fmt=fmt)  # type: ignore[arg-type]
    typer.echo(f"OK -> {out_path}")


@app.command()
def run_file(
    sql_file: Path = typer.Argument(..., help="Ruta del archivo .sql a ejecutar."),
    db: str = typer.Option(..., "--db", help="Alias de base (ver con: redshift-extractor ls)"),
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
    configure_logging()
    try:
        raw_sql = read_sql(sql_file)
        effective_limit = None if full else limit
        final_sql = apply_limit(raw_sql, effective_limit)

        mode = "FULL" if full else f"LIMIT {limit}"
        typer.echo(f"Conexion: {db}")
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
            connection=db,
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
    except Exception as error:
        typer.echo(f"ERROR - {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1)
