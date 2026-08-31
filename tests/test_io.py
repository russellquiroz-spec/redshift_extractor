"""Persistencia opcional y el extra `parquet` (A8, E7)."""

from __future__ import annotations

from importlib.util import find_spec

import pandas as pd
import pytest

from redshift_extractor.io import PARQUET_HINT, save_dataframe, write_parquet

TIENE_PYARROW = find_spec("pyarrow") is not None


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})


def test_guarda_csv(df, tmp_path):
    ruta = save_dataframe(df, str(tmp_path / "salida.csv"), fmt="csv")
    assert pd.read_csv(ruta).equals(df)


def test_formato_invalido_truena(df, tmp_path):
    with pytest.raises(ValueError, match="csv"):
        save_dataframe(df, str(tmp_path / "x.txt"), fmt="xlsx")  # type: ignore[arg-type]


def test_crea_el_directorio_destino(df, tmp_path):
    ruta = save_dataframe(df, str(tmp_path / "sub" / "dir" / "salida.csv"), fmt="csv")
    assert (tmp_path / "sub" / "dir" / "salida.csv").exists()
    assert ruta.endswith("salida.csv")


@pytest.mark.skipif(TIENE_PYARROW, reason="pyarrow instalado: no se puede probar su ausencia")
def test_parquet_sin_pyarrow_dice_como_instalar_el_extra(df, tmp_path):
    """
    A8: pyarrow salio de las dependencias duras en 0.2.0, asi que pedir parquet sin el
    extra tiene que decir como instalarlo, no reventar con un ImportError de pandas.
    """
    with pytest.raises(ImportError) as excinfo:
        write_parquet(df, tmp_path / "salida.parquet")
    assert "redshift-extractor[parquet]" in str(excinfo.value)


@pytest.mark.skipif(not TIENE_PYARROW, reason="requiere pyarrow (extra parquet)")
def test_parquet_funciona_con_el_extra_instalado(df, tmp_path):
    ruta = save_dataframe(df, str(tmp_path / "salida.parquet"), fmt="parquet")
    assert pd.read_parquet(ruta).equals(df)


def test_el_mensaje_del_extra_menciona_el_comando():
    assert 'pip install "redshift-extractor[parquet]"' in PARQUET_HINT
