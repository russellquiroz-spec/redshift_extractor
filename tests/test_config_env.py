"""Lectura del env propio: BOM (DE-1), fail-fast (C7, F5) y campos del tunel."""

from __future__ import annotations

import pytest

from redshift_extractor.config import load_config, load_full_config, read_env_file
from redshift_extractor.errors import ConfigError, EnvFileNotFoundError


def test_bom_truena_con_mensaje_que_dice_como_arreglarlo(write_env, minimal_env):
    """
    DE-1: antes se leia con utf-8-sig, que acepta el BOM en silencio. El sintoma que
    produce —la primera variable se lee vacia— es de los mas caros de diagnosticar.
    """
    path = write_env(minimal_env, bom=True)
    with pytest.raises(ConfigError) as excinfo:
        read_env_file(path)
    mensaje = str(excinfo.value)
    assert "BOM" in mensaje
    assert "SIN BOM" in mensaje
    assert "PowerShell" in mensaje


def test_sin_bom_carga_normal(write_env, minimal_env):
    write_env(minimal_env)
    ssh, rs_map = load_config()
    assert ssh.host == "bastion.example.test"
    assert sorted(rs_map) == ["dev", "prod"]


def test_env_file_inexistente_da_error_tipado(monkeypatch, tmp_path):
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(tmp_path / "no-existe"))
    with pytest.raises(EnvFileNotFoundError):
        load_config()


def test_clave_de_alias_mal_escrita_no_se_ignora_en_silencio(write_env, minimal_env):
    """
    C7: el caso tipico es el campo en minusculas. El usuario cree que configuro el
    host y en realidad no configuro nada.
    """
    write_env(minimal_env + "\nREDSHIFT__prod__host=otro-cluster.example.test\n")
    with pytest.raises(ConfigError, match="REDSHIFT__<alias>__<CAMPO>"):
        load_config()


def test_puerto_no_numerico_truena_al_cargar(write_env, minimal_env):
    """F5: fail-fast. La config invalida truena al cargar, no en la primera consulta."""
    write_env(minimal_env.replace("REDSHIFT__prod__PORT=5439", "REDSHIFT__prod__PORT=cinco"))
    with pytest.raises(ConfigError, match="PORT"):
        load_config()


def test_alias_se_normaliza_a_lowercase(write_env, minimal_env):
    """C5."""
    write_env(
        minimal_env
        + "\nREDSHIFT__STAGING__HOST=h\nREDSHIFT__STAGING__PORT=5439\n"
        + "REDSHIFT__STAGING__DBNAME=d\nREDSHIFT__STAGING__USER=u\n"
        + "REDSHIFT__STAGING__PASSWORD=p\n"
    )
    _ssh, rs_map = load_config()
    assert "staging" in rs_map
    assert "STAGING" not in rs_map


def test_campos_del_tunel_tienen_defaults_del_ecosistema(write_env, minimal_env):
    write_env(minimal_env)
    _app, ssh, _rs_map = load_full_config()
    assert ssh.local_port == 0
    assert ssh.connect_timeout_s == 15.0
    assert ssh.keepalive_s == 30.0
    assert ssh.known_hosts_path is None
    assert ssh.host_fingerprints == ()


def test_fingerprint_se_normaliza_desde_la_linea_de_ssh_keygen(write_env, minimal_env):
    base = "A" * 43
    write_env(minimal_env + f"\nSSH_HOST_FINGERPRINT=256 SHA256:{base} 10.0.0.1 (ED25519)\n")
    _app, ssh, _rs_map = load_full_config()
    assert ssh.host_fingerprints == (f"SHA256:{base}",)


def test_fingerprint_md5_se_rechaza(write_env, minimal_env):
    write_env(minimal_env + "\nSSH_HOST_FINGERPRINT=MD5:aa:bb:cc\n")
    with pytest.raises(ConfigError, match="MD5"):
        load_full_config()


def test_fingerprint_cortado_no_se_acepta_truncado(write_env, minimal_env):
    """Un copy/paste cortado deja la verificacion mas debil de lo que el usuario cree."""
    write_env(minimal_env + "\nSSH_HOST_FINGERPRINT=SHA256:" + "A" * 40 + "\n")
    with pytest.raises(ConfigError, match="43 caracteres"):
        load_full_config()


def test_varios_fingerprints_se_aceptan_separados_por_coma(write_env, minimal_env):
    uno, dos = "A" * 43, "B" * 43
    write_env(minimal_env + f"\nSSH_HOST_FINGERPRINT=SHA256:{uno},SHA256:{dos}\n")
    _app, ssh, _rs_map = load_full_config()
    assert ssh.host_fingerprints == (f"SHA256:{uno}", f"SHA256:{dos}")


def test_load_config_conserva_la_tupla_de_dos(write_env, minimal_env):
    """E8: cambiar la aridad romperia todo `ssh, rs_map = load_config()` de los hosts."""
    write_env(minimal_env)
    resultado = load_config()
    assert len(resultado) == 2
    ssh, rs_map = resultado
    assert ssh.user == "tester"
    assert isinstance(rs_map, dict)


def test_config_loaded_reporta_el_alias_default(write_env, minimal_env, events_log):
    collected, collect = events_log
    write_env(minimal_env)
    load_full_config(on_event=collect)
    eventos = [e for e in collected if e["event"] == "CONFIG_LOADED"]
    assert eventos
    assert eventos[0]["default_alias"] == "prod"
    assert eventos[0]["aliases"] == 2


def test_las_credenciales_no_aparecen_en_los_eventos(write_env, minimal_env, events_log):
    """La contrasena del env se registra como secreto y se tacha si se filtra."""
    collected, collect = events_log
    write_env(minimal_env)
    load_full_config(on_event=collect)

    from redshift_extractor.events import emit

    emit(collect, level="ERROR", event="ERROR", message="fallo con pass-lectura dentro")
    assert "pass-lectura" not in collected[-1]["message"]
