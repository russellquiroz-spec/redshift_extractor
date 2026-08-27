"""
DE-2: `alias` es el nombre canonico del alias de conexion en todo el ecosistema.

Las formas de 0.1.0 (`db=`, el alias como primer posicional, `list_databases()`,
`--db`) se retiraron en 0.3.0. Este archivo fija las dos mitades del contrato: que
`alias` esta en todas las funciones publicas, y que las formas viejas ya no existen.

El parametrizado de arriba es el patron copiado de
`postgresql_extractor_uploader/tests/test_alias_canon.py`: falla si alguna funcion
publica pierde `alias` o si alguna reintroduce `db`.
"""

from __future__ import annotations

import inspect

import pytest
from typer.testing import CliRunner

import redshift_extractor as rse
from redshift_extractor import config as config_mod
from redshift_extractor import extractor as extractor_mod
from redshift_extractor.cli import EXIT_OK, app
from redshift_extractor.errors import ConfigError

runner = CliRunner()

#: Las funciones publicas que aceptan un alias de conexion.
FUNCIONES_CON_ALIAS = [
    rse.extract_sql,
    rse.ping,
    config_mod.resolve,
]


@pytest.mark.parametrize("func", FUNCIONES_CON_ALIAS, ids=lambda f: f.__name__)
def test_toda_funcion_publica_acepta_alias(func):
    params = inspect.signature(func).parameters
    assert "alias" in params, f"{func.__name__} no acepta 'alias'"


@pytest.mark.parametrize("func", FUNCIONES_CON_ALIAS, ids=lambda f: f.__name__)
def test_ninguna_funcion_publica_reintroduce_db(func):
    params = inspect.signature(func).parameters
    assert "db" not in params, f"{func.__name__} volvio a aceptar 'db'"


def test_alias_de_extract_sql_es_keyword_only():
    """E2: el alias es keyword-only y toma su default de DEFAULT_ALIAS."""
    params = inspect.signature(rse.extract_sql).parameters
    assert params["alias"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["alias"].default is None


def test_query_es_el_primer_posicional():
    """E4: `query` posicional opcional, el resto keyword-only."""
    params = list(inspect.signature(rse.extract_sql).parameters.values())
    assert params[0].name == "query"
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[0].default is None
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[1:])


def test_no_quedan_varargs_en_extract_sql():
    """
    Los `*args` existian solo para acomodar los posicionales de la firma vieja. Sin
    formas viejas no tienen razon de ser, y dejarlos permitiria pasar basura en
    silencio.
    """
    kinds = [p.kind for p in inspect.signature(rse.extract_sql).parameters.values()]
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


def test_list_aliases_es_el_listador_canonico(write_env, minimal_env):
    write_env(minimal_env)
    assert rse.list_aliases() == ["dev", "prod"]


def test_default_alias_se_toma_del_env(write_env, minimal_env):
    write_env(minimal_env)
    app_cfg, _ssh, _alias, cfg = config_mod.resolve()
    assert app_cfg.default_alias == "prod"
    assert cfg.dbname == "analytics"


def test_sin_alias_y_sin_default_explica_como_arreglarlo(write_env, minimal_env):
    write_env(minimal_env.replace("DEFAULT_ALIAS=prod", ""))
    with pytest.raises(ConfigError, match="DEFAULT_ALIAS"):
        config_mod.resolve()


def test_alias_por_keyword_resuelve(write_env, minimal_env, recwarn):
    write_env(minimal_env)
    _app, _ssh, resolved, _cfg = config_mod.resolve(alias="dev")
    assert resolved == "dev"
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_alias_posicional_de_resolve_sigue_siendo_posicional(write_env, minimal_env):
    """Donde el alias ya era el primer posicional, lo sigue siendo: solo cambio de nombre."""
    write_env(minimal_env)
    _app, _ssh, resolved, _cfg = config_mod.resolve("dev")
    assert resolved == "dev"


# -----------------------------------------------------------------------------
# Las formas viejas ya no existen (retiradas en 0.3.0)
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("nombre", ["list_databases", "list_available_databases"])
def test_los_listadores_viejos_ya_no_existen(nombre):
    assert not hasattr(rse, nombre)
    assert not hasattr(extractor_mod, nombre)


def test_el_helper_de_compatibilidad_ya_no_existe():
    """`resolve_alias_arg` solo servia para aceptar `db=`."""
    assert not hasattr(config_mod, "resolve_alias_arg")
    assert not hasattr(config_mod, "DEPRECATED_DB_REMOVED_IN")


def test_el_shim_de_credentials_ya_no_existe():
    """D2: el shim delegaba en secret_loader y se retiro en 0.3.0."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("redshift_extractor.credentials")


def test_alias_posicional_de_extract_sql_es_error(write_env, minimal_env):
    """`extract_sql("prod", "select 1")` era la forma de 0.1.0."""
    write_env(minimal_env)
    with pytest.raises(TypeError):
        rse.extract_sql("prod", "select 1")  # type: ignore[misc]


def test_db_en_extract_sql_es_error(write_env, minimal_env):
    write_env(minimal_env)
    with pytest.raises(TypeError):
        rse.extract_sql(query="select 1", db="prod")  # type: ignore[call-arg]


def test_db_en_ping_es_error(write_env, minimal_env):
    write_env(minimal_env)
    with pytest.raises(TypeError):
        rse.ping(db="prod")  # type: ignore[call-arg]


def test_el_primer_posicional_ahora_es_el_query(write_env, minimal_env):
    """
    El cambio de significado que hay que cuidar: un `extract_sql("prod")` viejo ya no
    resuelve un alias, ahora manda "prod" como SQL. No se puede detectar sin conectar,
    asi que el sintoma es un error del cluster y no de la libreria; por eso el retiro
    va en un mayor de version.
    """
    write_env(minimal_env)
    with pytest.raises(ConfigError, match="no existe"):
        rse.extract_sql("select 1", alias="no-existe")


# -----------------------------------------------------------------------------
# Cambios sin forma vieja, a proposito
# -----------------------------------------------------------------------------
def _lineas(func) -> list:
    return [linea.strip() for linea in inspect.getsource(func).splitlines()]


def test_los_eventos_reportan_alias_no_db():
    lineas = _lineas(rse.extract_sql)
    assert "alias=resolved," in lineas
    assert not [linea for linea in lineas if linea.startswith("db=")]


def test_ping_reporta_la_llave_alias():
    lineas = _lineas(rse.ping)
    assert '"alias": resolved,' in lineas
    assert not [linea for linea in lineas if linea.startswith('"db"')]


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def test_cli_alias_es_el_canonico(write_env, minimal_env, tmp_path):
    write_env(minimal_env)
    sql = tmp_path / "q.sql"
    sql.write_text("select 1", encoding="utf-8")
    resultado = runner.invoke(app, ["run-file", str(sql), "--alias", "prod", "--dry-run"])
    assert resultado.exit_code == EXIT_OK
    assert "Conexion: prod" in resultado.output


def test_cli_sin_alias_usa_el_default(write_env, minimal_env, tmp_path):
    write_env(minimal_env)
    sql = tmp_path / "q.sql"
    sql.write_text("select 1", encoding="utf-8")
    resultado = runner.invoke(app, ["run-file", str(sql), "--dry-run"])
    assert resultado.exit_code == EXIT_OK
    assert "DEFAULT_ALIAS" in resultado.output


def test_cli_db_ya_no_se_acepta(write_env, minimal_env, tmp_path):
    write_env(minimal_env)
    sql = tmp_path / "q.sql"
    sql.write_text("select 1", encoding="utf-8")
    resultado = runner.invoke(app, ["run-file", str(sql), "--db", "prod", "--dry-run"])
    # 2 es el codigo de click para un error de uso: la opcion ya no existe.
    assert resultado.exit_code == 2


def _opciones_declaradas(comando: str) -> dict:
    """
    {declaracion: oculta?} de las opciones del comando, leidas de click.

    Se introspecciona en vez de leer el texto de `--help` porque typer lo dibuja con
    rich: el ancho de la terminal y los codigos de color parten los nombres, asi que
    un `"--alias" in output` pasa en una maquina y falla en otra. Lo que el contrato
    exige es que la opcion este declarada y visible, no como se renderiza.
    """
    import click
    import typer.main

    grupo = typer.main.get_command(app)
    assert isinstance(grupo, click.Group)
    declaradas = {}
    for parametro in grupo.commands[comando].params:
        for declaracion in getattr(parametro, "opts", []):
            declaradas[declaracion] = bool(getattr(parametro, "hidden", False))
    return declaradas


@pytest.mark.parametrize("comando", ["ping", "fingerprint", "run", "run-file"])
def test_cli_todo_comando_con_alias_declara_alias_visible_y_no_db(comando):
    opciones = _opciones_declaradas(comando)
    assert "--alias" in opciones, opciones
    assert opciones["--alias"] is False, "--alias debe salir en el --help"
    assert "--db" not in opciones, "--db se retiro en 0.3.0"
