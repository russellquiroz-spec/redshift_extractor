from __future__ import annotations

import os
from pathlib import Path

import typer

from redshift_extractor.extractor import extract_sql, list_databases
from redshift_extractor.io import save_dataframe
from redshift_extractor.logging import configure_logging

app = typer.Typer(add_completion=False)


@app.command()
def ls():
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
):
    """
    Ejecuta un query y guarda el resultado a archivo.
    """
    configure_logging()
    df = extract_sql(db=db, query=query)
    out_path = save_dataframe(df, out, fmt=fmt)  # type: ignore[arg-type]
    typer.echo(f"OK -> {out_path}")