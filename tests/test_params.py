"""
`params` enlazados en `extract_sql` (pendiente C).

Sin esto, toda consulta con valores variables se arma con `format` o f-strings. Con
fechas generadas en el codigo es inofensivo; deja de serlo el dia que un valor venga de
entrada de usuario, porque entonces es inyeccion de SQL.
"""

from __future__ import annotations

import pandas as pd
import pytest

from redshift_extractor import extractor as extractor_mod


class _ConexionFalsa:
    def close(self) -> None:
        pass


@pytest.fixture
def read_sql_espiado(monkeypatch):
    """
    Captura como se llama a `pd.read_sql`, sin cluster.

    Devuelve la lista de llamadas: cada una es `(sql, kwargs)`.
    """
    llamadas: list = []

    def espia(sql, conn, **kwargs):
        llamadas.append((sql, kwargs))
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(extractor_mod, "_connect", lambda rs, puerto: _ConexionFalsa())
    monkeypatch.setattr(extractor_mod.pd, "read_sql", espia)
    return llamadas


# ---------------------------------------------------------------------------
# La firma
# ---------------------------------------------------------------------------


def test_extract_sql_acepta_params_como_keyword():
    import inspect

    parametro = inspect.signature(extractor_mod.extract_sql).parameters["params"]
    assert parametro.kind is inspect.Parameter.KEYWORD_ONLY
    assert parametro.default is None


# ---------------------------------------------------------------------------
# El enlace
# ---------------------------------------------------------------------------


def test_los_params_llegan_enlazados_y_no_interpolados(tunnel_env, read_sql_espiado):
    """
    Lo que importa: el SQL llega **con el marcador intacto** y los valores viajan
    aparte. Si en vez de enlazarlos se interpolaran, el valor apareceria dentro del SQL.
    """
    tunnel_env()
    sql = "select * from ventas where ruta_id = %(ruta)s and fecha >= %(desde)s"

    extractor_mod.extract_sql(sql, params={"ruta": 42, "desde": "2026-01-01"})

    enviado, kwargs = read_sql_espiado[0]
    assert enviado == sql
    assert kwargs["params"] == {"ruta": 42, "desde": "2026-01-01"}
    assert "42" not in enviado
    assert "2026-01-01" not in enviado


def test_un_valor_malicioso_no_se_convierte_en_sql(tunnel_env, read_sql_espiado):
    """La razon de ser de C: un filtro que venga de fuera del codigo."""
    tunnel_env()
    veneno = "1; drop table ventas--"

    extractor_mod.extract_sql(
        "select * from ventas where ruta_id = %(ruta)s", params={"ruta": veneno}
    )

    enviado, kwargs = read_sql_espiado[0]
    assert "drop table" not in enviado
    assert kwargs["params"]["ruta"] == veneno


def test_params_funciona_con_query_file(tunnel_env, read_sql_espiado, tmp_path):
    tunnel_env()
    archivo = tmp_path / "q.sql"
    archivo.write_text("-- por ruta\nselect * from v where r = %(ruta)s", encoding="utf-8")

    extractor_mod.extract_sql(query_file=str(archivo), params={"ruta": 7})

    _enviado, kwargs = read_sql_espiado[0]
    assert kwargs["params"] == {"ruta": 7}


# ---------------------------------------------------------------------------
# Que `params=None` no cambie nada: la parte delicada
# ---------------------------------------------------------------------------


def test_sin_params_no_se_le_pasa_el_argumento_a_read_sql(tunnel_env, read_sql_espiado):
    """
    psycopg2 solo interpreta `%` cuando recibe parametros. Pasarle `params=None`
    explicito le cambiaria el significado a un SQL con `%` literales que hoy funciona,
    asi que la llamada sin params tiene que ir exactamente como iba antes.
    """
    tunnel_env()
    extractor_mod.extract_sql("select 1")

    _enviado, kwargs = read_sql_espiado[0]
    assert "params" not in kwargs


@pytest.mark.parametrize(
    "sql",
    [
        "select * from clientes where nombre like '%rabbit%'",
        "select to_char(fecha, '%Y-%m') from ventas",
        "select 50 % 7",
    ],
)
def test_el_sql_con_porcentajes_literales_sigue_pasando_intacto(
    tunnel_env, read_sql_espiado, sql
):
    tunnel_env()
    extractor_mod.extract_sql(sql)

    enviado, kwargs = read_sql_espiado[0]
    assert enviado == sql
    assert "params" not in kwargs


def test_params_vacio_si_se_pasa_a_read_sql(tunnel_env, read_sql_espiado):
    """
    Un dict vacio no es lo mismo que None: quien lo pase esta diciendo "este SQL lleva
    marcadores", aunque hoy no haya ninguno. Se respeta tal cual.
    """
    tunnel_env()
    extractor_mod.extract_sql("select 1", params={})

    _enviado, kwargs = read_sql_espiado[0]
    assert kwargs["params"] == {}
