"""
Pruebas de las funciones puras de `cli.py` (pendiente G).

El CLI es la superficie que una persona usa a mano y la que corre en tareas
programadas, y es la unica parte de la libreria que se puede probar entera sin red.
Estaba en cero cobertura, y por eso el bug del pendiente E —el modo prueba rechazando
cualquier `.sql` que empiece con comentario— vivio hasta encontrarse a mano contra
produccion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
import typer

from redshift_extractor import cli as cli_mod
from redshift_extractor.cli import (
    apply_limit,
    execute_with_retries,
    first_keyword,
    is_connection_error,
    print_result,
    read_sql,
    strip_trailing_semicolons,
)

# ---------------------------------------------------------------------------
# first_keyword / apply_limit: el pendiente E
# ---------------------------------------------------------------------------

#: (sql, primera palabra esperada). El caso que motivo E es el primero: un header de
#: comentarios, que es como se escribe toda query documentada.
CASOS_PRIMERA_PALABRA = [
    ("-- Bono por ruta: cumplimiento v3\n-- Grano: ruta_id\nselect 1", "select"),
    ("/* comentario de bloque */ with x as (select 1) select * from x", "with"),
    ("--a\n/* b */\n  -- c\nSELECT 1", "select"),
    ("   \n\t select 1", "select"),
    ("insert into t values (1)", "insert"),
    ("update t set a = 1", "update"),
    # Un `--` dentro de una cadena literal no abre comentario: para cuando aparece,
    # el primer token ya se tomo.
    ("select '--esto no es comentario'", "select"),
    # Solo comentarios, o un bloque sin cerrar: no hay sentencia que envolver.
    ("-- solo comentarios\n-- nada mas\n", ""),
    ("/* bloque sin cerrar select 1", ""),
    ("", ""),
]


@pytest.mark.parametrize("sql,esperada", CASOS_PRIMERA_PALABRA)
def test_first_keyword_ignora_comentarios(sql, esperada):
    assert first_keyword(sql) == esperada


def test_apply_limit_acepta_sql_con_header_de_comentarios():
    """El caso exacto de E: antes esto era un ValueError."""
    sql = "-- Bono por ruta: cumplimiento v3\n-- Grano: no_semana, ruta_id\nselect 1"
    resultado = apply_limit(sql, 10)

    assert resultado.startswith("SELECT *")
    assert resultado.endswith("LIMIT 10")
    # El SQL que se ejecuta conserva sus comentarios: solo la decision los ignora.
    assert "-- Bono por ruta" in resultado


def test_apply_limit_acepta_with_despues_de_comentario_de_bloque():
    resultado = apply_limit("/* header */\nwith x as (select 1) select * from x", 5)
    assert resultado.endswith("LIMIT 5")
    assert "/* header */" in resultado


def test_apply_limit_quita_el_punto_y_coma_final():
    resultado = apply_limit("-- header\nselect 1;\n", 10)
    assert "select 1\n) AS query_limitada" in resultado


def test_apply_limit_conserva_los_comentarios_interiores():
    sql = "select 1 -- comentario interior\nfrom dual"
    assert "-- comentario interior" in apply_limit(sql, 10)


@pytest.mark.parametrize("sentencia", ["insert into t values (1)", "update t set a = 1"])
def test_apply_limit_sigue_rechazando_lo_que_no_es_select(sentencia):
    """
    El rechazo tiene que seguir vivo cuando deje de ocurrir por accidente: antes
    cualquier header de comentarios lo disparaba, asi que no probaba nada.
    """
    with pytest.raises(ValueError, match="SELECT/WITH"):
        apply_limit(sentencia, 10)


def test_apply_limit_rechaza_lo_que_no_es_select_aunque_traiga_header():
    with pytest.raises(ValueError, match="empieza con 'insert'"):
        apply_limit("-- carga diaria\ninsert into t values (1)", 10)


def test_apply_limit_rechaza_archivo_de_puros_comentarios():
    with pytest.raises(ValueError, match="solo comentarios"):
        apply_limit("-- pendiente de escribir\n", 10)


def test_apply_limit_rechaza_archivo_vacio():
    with pytest.raises(ValueError, match="vacio"):
        apply_limit("   \n  ", 10)


@pytest.mark.parametrize("limite", [0, -1, -100])
def test_apply_limit_rechaza_limite_no_positivo(limite):
    with pytest.raises(ValueError, match="mayor a 0"):
        apply_limit("select 1", limite)


def test_apply_limit_sin_limite_devuelve_el_sql_tal_cual():
    """`--full` pasa limit=None: ahi no se valida el tipo de sentencia."""
    assert apply_limit("insert into t values (1);", None) == "insert into t values (1)"


# ---------------------------------------------------------------------------
# strip_trailing_semicolons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("select 1;", "select 1"),
        ("select 1;;;", "select 1"),
        ("  select 1 ;  ;  ", "select 1"),
        ("select 1", "select 1"),
        ("", ""),
    ],
)
def test_strip_trailing_semicolons(entrada, esperado):
    assert strip_trailing_semicolons(entrada) == esperado


# ---------------------------------------------------------------------------
# read_sql
# ---------------------------------------------------------------------------


def test_read_sql_lee_utf8_con_acentos(tmp_path):
    archivo = tmp_path / "q.sql"
    archivo.write_text("-- Facturacion por region\nselect 'ñandú' as bicho", encoding="utf-8")
    assert "ñandú" in read_sql(archivo)


def test_read_sql_de_archivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError, match="No existe el archivo"):
        read_sql(tmp_path / "no-existe.sql")


def test_read_sql_de_un_directorio(tmp_path):
    carpeta = tmp_path / "queries"
    carpeta.mkdir()
    with pytest.raises(ValueError, match="No es un archivo"):
        read_sql(carpeta)


# ---------------------------------------------------------------------------
# is_connection_error
# ---------------------------------------------------------------------------

#: Decide si `run-file` reintenta. Un falso negativo desperdicia los reintentos; un
#: falso positivo reintenta tres veces un error de SQL que nunca va a cambiar.
ERRORES_DE_CONEXION = [
    RuntimeError("Could not establish connection to the bastion"),
    RuntimeError("connection refused"),
    RuntimeError("Connection reset by peer"),
    RuntimeError("server closed the connection unexpectedly"),
    RuntimeError("SSH negotiation failed"),
    RuntimeError("el tunnel no contesta"),
    RuntimeError("operation timed out"),
]

ERRORES_QUE_NO_SON_DE_CONEXION = [
    ValueError("column 'ruta_id' does not exist"),
    RuntimeError("syntax error at or near select"),
    ValueError("permission denied for relation ventas"),
]


@pytest.mark.parametrize("error", ERRORES_DE_CONEXION, ids=lambda e: str(e)[:28])
def test_is_connection_error_reconoce_fallos_de_red(error):
    assert is_connection_error(error)


@pytest.mark.parametrize(
    "error", ERRORES_QUE_NO_SON_DE_CONEXION, ids=lambda e: str(e)[:28]
)
def test_is_connection_error_no_confunde_errores_de_sql(error):
    assert not is_connection_error(error)


def test_is_connection_error_mira_tambien_el_nombre_de_la_clase():
    """`OperationalError` de psycopg2 no siempre trae texto reconocible."""

    class OperationalError(Exception):
        pass

    assert is_connection_error(OperationalError("algo"))


# ---------------------------------------------------------------------------
# execute_with_retries
# ---------------------------------------------------------------------------


@pytest.fixture
def sin_espera(monkeypatch):
    """Quita el sleep entre reintentos: los tests no tienen por que tardar 5s."""
    monkeypatch.setattr(cli_mod.time, "sleep", lambda _s: None)


def test_execute_with_retries_devuelve_a_la_primera(monkeypatch, sin_espera):
    llamadas = []

    def falso_extract(sql, *, alias=None):
        llamadas.append(alias)
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(cli_mod, "extract_sql", falso_extract)

    df = execute_with_retries(connection="prod", sql="select 1", retries=3, retry_wait=0)
    assert len(df) == 1
    assert llamadas == ["prod"]


def test_execute_with_retries_reintenta_solo_los_fallos_de_conexion(monkeypatch, sin_espera):
    intentos = {"n": 0}

    def falso_extract(sql, *, alias=None):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("connection refused")
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(cli_mod, "extract_sql", falso_extract)

    df = execute_with_retries(connection=None, sql="select 1", retries=3, retry_wait=0)
    assert len(df) == 1
    assert intentos["n"] == 3


def test_execute_with_retries_no_reintenta_un_error_de_sql(monkeypatch, sin_espera):
    """Reintentar un error de sintaxis gasta tres viajes al cluster para nada."""
    intentos = {"n": 0}

    def falso_extract(sql, *, alias=None):
        intentos["n"] += 1
        raise ValueError("column 'ruta_id' does not exist")

    monkeypatch.setattr(cli_mod, "extract_sql", falso_extract)

    with pytest.raises(ValueError, match="ruta_id"):
        execute_with_retries(connection=None, sql="select 1", retries=3, retry_wait=0)
    assert intentos["n"] == 1


def test_execute_with_retries_agota_los_intentos_y_relanza(monkeypatch, sin_espera):
    intentos = {"n": 0}

    def falso_extract(sql, *, alias=None):
        intentos["n"] += 1
        raise RuntimeError("connection refused")

    monkeypatch.setattr(cli_mod, "extract_sql", falso_extract)

    with pytest.raises(RuntimeError, match="connection refused"):
        execute_with_retries(connection=None, sql="select 1", retries=2, retry_wait=0)
    assert intentos["n"] == 2


@pytest.mark.parametrize("retries", [0, -1])
def test_execute_with_retries_rechaza_intentos_no_positivos(retries):
    with pytest.raises(ValueError, match="--retries debe ser mayor a 0"):
        execute_with_retries(connection=None, sql="select 1", retries=retries, retry_wait=0)


# ---------------------------------------------------------------------------
# print_result
# ---------------------------------------------------------------------------


def test_print_result_reporta_forma_y_tiempo(capsys):
    df = pd.DataFrame({"ruta_id": range(3), "monto": [1.0, 2.0, 3.0]})
    print_result(df, 4.25)

    salida = capsys.readouterr().out
    assert "4.2s" in salida
    assert "Filas: 3" in salida
    assert "Columnas: 2" in salida
    assert "ruta_id" in salida


def test_print_result_solo_muestra_una_muestra_de_filas(capsys):
    df = pd.DataFrame({"n": range(100)})
    print_result(df, 1.0)

    salida = capsys.readouterr().out
    assert "Filas: 100" in salida
    # Se imprimen DEFAULT_LIMIT filas, no las 100.
    assert salida.count("\n") < 30


def test_print_result_con_dataframe_vacio(capsys):
    print_result(pd.DataFrame(), 0.5)
    salida = capsys.readouterr().out
    assert "Filas: 0" in salida
    assert "Columnas: 0" in salida


def test_print_result_separa_miles(capsys):
    print_result(pd.DataFrame({"n": range(1500)}), 1.0)
    assert "Filas: 1,500" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# F4: codigos de salida del entry point real
# ---------------------------------------------------------------------------
#
# Se corre `main()` y no `app` con `CliRunner`: la separacion del codigo de uso vive en
# `main()`, asi que con `CliRunner` sobre `app` no se ejercitaria y estos tests pasarian
# sin probar nada.


def _salida_de(monkeypatch, argv: list[str]) -> int:
    """Corre `cli.main()` con ese argv y devuelve el codigo con el que salio."""
    monkeypatch.setattr(sys, "argv", ["redshift-extractor", *argv])
    with pytest.raises(SystemExit) as salida:
        cli_mod.main()
    return salida.value.code


@pytest.mark.parametrize(
    "argv,motivo",
    [
        (["ls", "--alais", "prod"], "flag mal escrito"),
        (["subcomando-inexistente"], "subcomando que no existe"),
        (["run-file"], "argumento obligatorio faltante"),
        (["run-file", "q.sql", "--limit"], "opcion sin su valor"),
    ],
)
def test_error_de_uso_sale_con_64(monkeypatch, argv, motivo):
    """
    F4: los errores de USO se separan de los de configuracion.

    click sale con 2 por su cuenta ante un flag invalido, y 2 es el codigo que el
    ecosistema le asigno a los errores de configuracion. Un runner que trate el 2 como
    "revisa el .env" se equivocaba ante cualquier typo en la linea de comandos.
    """
    assert _salida_de(monkeypatch, argv) == cli_mod.EXIT_USAGE, motivo


def test_error_de_configuracion_sigue_saliendo_con_2(monkeypatch, tmp_path):
    """
    La trampa de `standalone_mode=False`: click **devuelve** el codigo de un
    `typer.Exit` en vez de levantarlo. Tratarlo como excepcion hace que todos los
    errores salgan con 0.
    """
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(tmp_path / "no-existe.env"))
    assert _salida_de(monkeypatch, ["ls"]) == cli_mod.EXIT_CONFIG


def test_alias_inexistente_sale_con_2(monkeypatch, write_env, minimal_env):
    write_env(minimal_env)
    assert _salida_de(monkeypatch, ["ping", "--alias", "no-existe"]) == cli_mod.EXIT_CONFIG


def test_error_de_negocio_sale_con_1(monkeypatch, write_env, minimal_env, tmp_path):
    """`negocio=1`, no `4`: es lo que hacen las cuatro librerias del ecosistema."""
    write_env(minimal_env)
    sql = tmp_path / "q.sql"
    sql.write_text("-- header\ninsert into t values (1)", encoding="utf-8")
    # `apply_limit` rechaza el insert antes de tocar la red: error de negocio.
    assert _salida_de(monkeypatch, ["run-file", str(sql)]) == cli_mod.EXIT_BUSINESS


def test_ctrl_c_sale_con_130(monkeypatch, write_env, minimal_env):
    write_env(minimal_env)

    def aborta(**_kwargs):
        raise typer.Abort()

    monkeypatch.setattr(cli_mod, "app", aborta)
    assert _salida_de(monkeypatch, ["ls"]) == cli_mod.EXIT_INTERRUPTED


def test_las_clases_de_error_salen_de_la_api_publica_de_typer():
    """
    Regresion del CI: la primera version de esto sacaba las tres clases del modulo
    privado `typer._click.exceptions`, y duro una version de parche. typer 0.27.2 movio
    `Abort` a `typer.exceptions` y el CI truen0 con AttributeError.

    Lo estable es `typer.BadParameter` -cuyo MRO pasa por `UsageError` y
    `ClickException` vivan donde vivan- mas `typer.Abort`. Nada de modulos privados.
    """
    clases = cli_mod.clases_de_error()
    assert clases is not None

    usage_error, click_exception, abort = clases
    assert {c.__name__ for c in (usage_error, click_exception, abort)} == {
        "UsageError",
        "ClickException",
        "Abort",
    }
    # Tienen que ser las del click que typer usa de verdad, no las de otro paquete.
    assert issubclass(typer.BadParameter, usage_error)
    assert issubclass(usage_error, click_exception)
    assert abort is typer.Abort
    # Y no deben venir del modulo privado que se mueve entre parches.
    assert "_click.exceptions" not in abort.__module__


def test_mostrar_error_no_revienta_si_la_excepcion_no_trae_show(capsys):
    """
    Las clases llegan resueltas en runtime, asi que su interfaz no esta garantizada.
    Un AttributeError aqui reventaria dentro del propio manejador de errores.
    """

    class SinShow(Exception):
        pass

    cli_mod.mostrar_error(SinShow("algo trono"))
    assert "algo trono" in capsys.readouterr().err


def test_comando_exitoso_sale_con_0(monkeypatch, write_env, minimal_env):
    write_env(minimal_env)
    assert _salida_de(monkeypatch, ["ls"]) == cli_mod.EXIT_OK


def test_help_sale_con_0(monkeypatch):
    assert _salida_de(monkeypatch, ["--help"]) == cli_mod.EXIT_OK


def test_el_entry_point_apunta_a_main_y_no_a_app():
    """
    Si `[project.scripts]` vuelve a apuntar a `:app`, `main()` se salta y los errores de
    uso vuelven a salir con 2. El resto de estos tests seguiria en verde.
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    contenido = pyproject.read_text(encoding="utf-8")
    assert "redshift_extractor.cli:main" in contenido
    assert "redshift_extractor.cli:app" not in contenido


def test_main_no_truena_si_no_puede_resolver_las_excepciones(monkeypatch):
    """Sin poder distinguirlas, se cae al modo estandar de typer en vez de un traceback."""
    monkeypatch.setattr(cli_mod, "clases_de_error", lambda: None)
    llamadas = []
    monkeypatch.setattr(cli_mod, "app", lambda *a, **k: llamadas.append(k))
    monkeypatch.setattr(sys, "argv", ["redshift-extractor", "--help"])

    cli_mod.main()
    # Modo estandar: se llama sin `standalone_mode`, y click maneja la salida.
    assert llamadas == [{}]
