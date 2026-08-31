from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

Format = Literal["csv", "parquet"]

#: pyarrow vive en el extra `parquet` desde 0.3.0 (A8): son ~40 MB que solo hacen
#: falta para guardar Parquet. Si falta, el mensaje dice como instalarlo.
PARQUET_HINT = (
    'Parquet necesita pyarrow, que desde 0.3.0 vive en un extra. '
    'Instalalo con: pip install "redshift-extractor[parquet]"'
)


def write_parquet(df: pd.DataFrame, path: str | Path, index: bool = False) -> None:
    """Escribe Parquet traduciendo la falta de pyarrow a un mensaje accionable."""
    try:
        df.to_parquet(path, index=index)
    except ImportError as exc:
        raise ImportError(f"{PARQUET_HINT}. Detalle: {exc}") from exc


def save_dataframe(
    df: pd.DataFrame,
    output_path: str,
    fmt: Format = "parquet",
    index: bool = False,
) -> str:
    """
    Guarda DataFrame en CSV o Parquet.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        df.to_csv(path, index=index)
    elif fmt == "parquet":
        write_parquet(df, path, index=index)
    else:
        raise ValueError("fmt debe ser 'csv' o 'parquet'")

    return str(path.resolve())


__all__ = ["PARQUET_HINT", "save_dataframe", "write_parquet"]
