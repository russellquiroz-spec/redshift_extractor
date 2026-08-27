"""
Tests del tunel contra el servidor SSH en proceso (K2).

Cubren lo que DE-4 mando portar: host key (I1), cierre garantizado (I4), health check
de protocolo (I5), errores tipados (I6), restore de las globales de sshtunnel (H7) y
el rodeo del deadlock en fallo de autenticacion. No hay tests de reuso ni de
`tunnel_status` porque I3 e I7 quedaron fuera a proposito.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import warnings

import pytest

from redshift_extractor import config as config_mod
from redshift_extractor.errors import (
    TunnelAuthError,
    TunnelBindError,
    TunnelError,
    TunnelHostKeyError,
    TunnelNetworkError,
)
from redshift_extractor.tunnel import (
    fetch_remote_host_key,
    fingerprint,
    open_tunnel,
    port_is_free,
    probe_redshift,
)
from fakepg import DumbTCPServer, FakePostgres
from sshserver import generate_client_key

pytestmark = pytest.mark.sshserver


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _cfg(alias: str = "prod"):
    _app, ssh, _resolved, rs = config_mod.resolve(alias)
    return ssh, rs


def _run_with_timeout(func, seconds: float = 30.0):
    """
    Corre `func` en un hilo y falla si no termina.

    Sin esto, una regresion del deadlock de sshtunnel colgaria la suite entera en vez
    de dar un test rojo.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = func()
        except BaseException as exc:  # noqa: BLE001 - se re-lanza en el hilo del test
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=seconds)
    assert not thread.is_alive(), (
        f"la operacion no termino en {seconds:g}s: probable deadlock de sshtunnel"
    )
    if "error" in box:
        raise box["error"]
    return box.get("result")


# -----------------------------------------------------------------------------
# Apertura, health check y cierre
# -----------------------------------------------------------------------------
def test_abre_el_tunel_y_el_puerto_responde(tunnel_env, events_log):
    collected, collect = events_log
    tunnel_env()
    ssh, rs = _cfg()

    with open_tunnel(ssh, rs, on_event=collect) as forwarder:
        puerto = forwarder.local_bind_port
        assert puerto > 0
        assert probe_redshift(puerto)

    eventos = [e["event"] for e in collected]
    assert "TUNNEL_START" in eventos
    assert "TUNNEL_READY" in eventos
    assert "TUNNEL_CLOSED" in eventos


def test_al_salir_del_context_manager_el_puerto_queda_libre(tunnel_env):
    """I4 por el camino normal, verificable con un bind."""
    tunnel_env()
    ssh, rs = _cfg()

    with open_tunnel(ssh, rs) as forwarder:
        puerto = forwarder.local_bind_port
        assert not port_is_free(puerto)

    time.sleep(0.3)
    assert port_is_free(puerto), f"el puerto {puerto} sigue ocupado despues del with"
    assert not probe_redshift(puerto)


def test_el_tunel_se_cierra_aunque_el_bloque_lance(tunnel_env):
    tunnel_env()
    ssh, rs = _cfg()
    puerto = None

    with pytest.raises(ZeroDivisionError):
        with open_tunnel(ssh, rs) as forwarder:
            puerto = forwarder.local_bind_port
            1 / 0

    time.sleep(0.3)
    assert puerto is not None
    assert port_is_free(puerto)


def test_puerto_local_efimero_por_default(tunnel_env):
    """
    H5: SSH_LOCAL_PORT=0 es el default y dos tuneles no se pisan.

    Los dos tuneles se abren a la vez a proposito. Abriendolos en secuencia, el
    sistema operativo puede reasignar el mismo puerto efimero al segundo -es libre de
    hacerlo, el primero ya lo solto- y la asercion dependeria de la politica de
    asignacion de puertos de cada plataforma en vez de la libreria.
    """
    tunnel_env()
    ssh, rs = _cfg()
    assert ssh.local_port == 0

    with open_tunnel(ssh, rs) as primero:
        with open_tunnel(ssh, rs) as segundo:
            assert primero.local_bind_port != segundo.local_bind_port
            assert probe_redshift(primero.local_bind_port)
            assert probe_redshift(segundo.local_bind_port)


def test_health_check_es_de_protocolo_no_solo_tcp(fake_redshift):
    """I5: un puerto que acepta TCP pero no contesta el SSLRequest no cuenta."""
    assert probe_redshift(fake_redshift.port)

    mudo = DumbTCPServer()
    puerto = mudo.start()
    try:
        assert not probe_redshift(puerto, timeout_s=1.0)
    finally:
        mudo.stop()

    assert not probe_redshift(_free_port(), timeout_s=1.0)
    assert not probe_redshift(0)


def test_tunel_al_puerto_equivocado_falla_al_abrir(tunnel_env):
    """
    I5 aplicado a la apertura: el tunel se abre igual contra un puerto donde no hay
    nada, y sin el health check el error aparecia despues como timeout de psycopg2.
    """
    mudo = DumbTCPServer()
    puerto_mudo = mudo.start()
    try:
        tunnel_env()
        ssh, _rs = _cfg()
        _app, _ssh, _resolved, rs = config_mod.resolve("prod")
        from dataclasses import replace

        rs_mal = replace(rs, port=puerto_mudo)
        with pytest.raises(TunnelError, match="no contesta un servidor Redshift"):
            with open_tunnel(ssh, rs_mal):
                pass
    finally:
        mudo.stop()


def test_cierre_al_terminar_el_proceso_por_excepcion(tunnel_env, tmp_path):
    """
    I4 en el caso que el `with` no cubre: el proceso muere por una excepcion no
    capturada y no debe quedar el socket vivo.
    """
    env_path = tunnel_env()
    script = tmp_path / "muere.py"
    script.write_text(
        textwrap.dedent(
            """
            from redshift_extractor import config as cfg
            from redshift_extractor.tunnel import open_tunnel

            _app, ssh, _alias, rs = cfg.resolve("prod")
            manager = open_tunnel(ssh, rs)
            forwarder = manager.__enter__()
            print(forwarder.local_bind_port, flush=True)
            raise RuntimeError("muerte no capturada")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    env = dict(os.environ, REDSHIFT_EXTRACTOR_ENV_FILE=str(env_path))
    proceso = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=120
    )
    assert proceso.returncode != 0, "el script debia morir por la excepcion"
    assert "RuntimeError" in proceso.stderr
    puerto = int(proceso.stdout.strip().splitlines()[0])

    time.sleep(0.5)
    assert port_is_free(puerto), f"quedo el puerto {puerto} ocupado tras morir el proceso"


def test_dos_procesos_concurrentes_con_puerto_efimero(tunnel_env, tmp_path):
    """H5: con SSH_LOCAL_PORT=0 dos procesos no se pisan."""
    env_path = tunnel_env(local_port=0)
    script = tmp_path / "abre.py"
    script.write_text(
        textwrap.dedent(
            """
            import time
            from redshift_extractor import config as cfg
            from redshift_extractor.tunnel import open_tunnel, probe_redshift

            _app, ssh, _alias, rs = cfg.resolve("prod")
            with open_tunnel(ssh, rs) as forwarder:
                assert probe_redshift(forwarder.local_bind_port)
                print(forwarder.local_bind_port, flush=True)
                time.sleep(2)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    env = dict(os.environ, REDSHIFT_EXTRACTOR_ENV_FILE=str(env_path))
    uno = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    dos = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    salida_uno = uno.communicate(timeout=120)
    salida_dos = dos.communicate(timeout=120)

    assert uno.returncode == 0, salida_uno[1]
    assert dos.returncode == 0, salida_dos[1]
    assert int(salida_uno[0].strip()) != int(salida_dos[0].strip())


# -----------------------------------------------------------------------------
# I6: cuatro modos de falla distinguibles
# -----------------------------------------------------------------------------
def test_los_cuatro_errores_son_clases_distintas():
    """Colapsarlos en un generico es motivo de rechazo."""
    tipos = (TunnelNetworkError, TunnelAuthError, TunnelHostKeyError, TunnelBindError)
    for tipo in tipos:
        otros = tuple(t for t in tipos if t is not tipo)
        assert not issubclass(tipo, otros)
        assert issubclass(tipo, TunnelError)


def test_bastion_inalcanzable_da_tunnel_network_error(tmp_path, monkeypatch, fake_redshift):
    puerto_cerrado = _free_port()
    contenido = textwrap.dedent(
        f"""
        SSH_HOST=127.0.0.1
        SSH_PORT={puerto_cerrado}
        SSH_USER=tester
        SSH_PKEY_PATH={tmp_path / 'no-importa'}
        SSH_CONNECT_TIMEOUT_S=3
        DEFAULT_ALIAS=prod
        REDSHIFT__prod__HOST=127.0.0.1
        REDSHIFT__prod__PORT={fake_redshift.port}
        REDSHIFT__prod__DBNAME=analytics
        REDSHIFT__prod__USER=u
        REDSHIFT__prod__PASSWORD=p
        """
    ).lstrip()
    path = tmp_path / ".env.redshift_extractor"
    path.write_bytes(contenido.encode("utf-8"))
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(path))

    ssh, rs = _cfg()
    with pytest.raises(TunnelNetworkError) as excinfo:
        with open_tunnel(ssh, rs):
            pass
    mensaje = str(excinfo.value)
    assert "127.0.0.1" in mensaje
    assert "sshd" in mensaje or "Security Group" in mensaje


def test_llave_invalida_da_tunnel_auth_error_sin_colgarse(tunnel_env, tmp_path):
    """
    I6 mas el rodeo del deadlock: con una llave que el bastion rechaza, sshtunnel
    0.4.0 deja el forward server sin su hilo `serve_forever` y el `stop()` espera para
    siempre. El test falla por timeout si esa vuelta se pierde.
    """
    _otra_llave, otra_ruta = generate_client_key(tmp_path / "otras", name="intrusa")
    tunnel_env(pkey=otra_ruta)
    ssh, rs = _cfg()

    def abrir() -> None:
        with open_tunnel(ssh, rs):
            pass

    with pytest.raises(TunnelAuthError) as excinfo:
        _run_with_timeout(abrir, seconds=45)
    assert "Autenticacion SSH rechazada" in str(excinfo.value)


def test_llave_inexistente_da_tunnel_auth_error(tunnel_env, tmp_path):
    tunnel_env(pkey=tmp_path / "no-existe")
    ssh, rs = _cfg()
    with pytest.raises(TunnelAuthError, match="no existe"):
        with open_tunnel(ssh, rs):
            pass


def test_puerto_local_ocupado_da_tunnel_bind_error(tunnel_env):
    ocupado = DumbTCPServer()
    puerto = ocupado.start()
    try:
        tunnel_env(local_port=puerto)
        ssh, rs = _cfg()
        with pytest.raises(TunnelBindError) as excinfo:
            with open_tunnel(ssh, rs):
                pass
        assert str(puerto) in str(excinfo.value)
        assert "SSH_LOCAL_PORT=0" in str(excinfo.value)
    finally:
        ocupado.stop()


# -----------------------------------------------------------------------------
# I1: host key
# -----------------------------------------------------------------------------
def test_host_desconocido_da_tunnel_host_key_error(tunnel_env, tmp_path):
    """I1: AutoAddPolicy esta prohibido, un host sin registrar no se acepta."""
    vacio = tmp_path / "known_hosts_vacio"
    vacio.write_text("", encoding="utf-8")
    tunnel_env(extra=f"\nSSH_KNOWN_HOSTS_PATH={vacio}\n")
    ssh, rs = _cfg()
    with pytest.raises(TunnelHostKeyError) as excinfo:
        with open_tunnel(ssh, rs):
            pass
    assert "ssh-keyscan" in str(excinfo.value)


def test_host_key_que_no_coincide_da_tunnel_host_key_error(tunnel_env, ssh_server, tmp_path):
    equivocada = ssh_server.write_wrong_known_hosts(tmp_path / "known_hosts_malo")
    tunnel_env(extra=f"\nSSH_KNOWN_HOSTS_PATH={equivocada}\n")
    ssh, rs = _cfg()
    with pytest.raises(TunnelHostKeyError):
        with open_tunnel(ssh, rs):
            pass


def test_verificacion_por_fingerprint(tunnel_env, ssh_server, tmp_path):
    """
    Con SSH_HOST_FINGERPRINT se verifica contra el hash y no se usa known_hosts.

    Es mas fuerte que el camino de known_hosts, donde el usuario agrega la entrada con
    ssh-keyscan (trust on first use, sin autenticar nada).
    """
    esperado = fingerprint(ssh_server.host_key)
    inexistente = tmp_path / "no-hay-known-hosts"
    tunnel_env(extra=f"\nSSH_KNOWN_HOSTS_PATH={inexistente}\nSSH_HOST_FINGERPRINT={esperado}\n")
    ssh, rs = _cfg()

    with open_tunnel(ssh, rs) as forwarder:
        assert probe_redshift(forwarder.local_bind_port)


def test_fingerprint_equivocado_da_tunnel_host_key_error(tunnel_env):
    otro = "SHA256:" + "A" * 43
    tunnel_env(extra=f"\nSSH_HOST_FINGERPRINT={otro}\n")
    ssh, rs = _cfg()

    with pytest.raises(TunnelHostKeyError) as excinfo:
        with open_tunnel(ssh, rs):
            pass

    mensaje = str(excinfo.value)
    assert "no coincide con ningun fingerprint" in mensaje
    assert "recibido" in mensaje
    assert otro in mensaje


def test_fingerprint_tiene_prioridad_sobre_known_hosts(tunnel_env, ssh_server, tmp_path):
    equivocada = ssh_server.write_wrong_known_hosts(tmp_path / "known_hosts_malo")
    tunnel_env(
        extra=(
            f"\nSSH_KNOWN_HOSTS_PATH={equivocada}"
            f"\nSSH_HOST_FINGERPRINT={fingerprint(ssh_server.host_key)}\n"
        )
    )
    ssh, rs = _cfg()
    with open_tunnel(ssh, rs) as forwarder:
        assert probe_redshift(forwarder.local_bind_port)


def test_varios_fingerprints_acepta_el_que_coincida(tunnel_env, ssh_server):
    correcto = fingerprint(ssh_server.host_key)
    tunnel_env(extra=f"\nSSH_HOST_FINGERPRINT=SHA256:{'B' * 43},{correcto}\n")
    ssh, rs = _cfg()
    with open_tunnel(ssh, rs) as forwarder:
        assert probe_redshift(forwarder.local_bind_port)


def test_fetch_remote_host_key_devuelve_la_llave_del_bastion(tunnel_env, ssh_server):
    tunnel_env()
    ssh, _rs = _cfg()
    key = fetch_remote_host_key(ssh)
    assert fingerprint(key) == fingerprint(ssh_server.host_key)
    assert key.get_name() == ssh_server.host_key.get_name()


# -----------------------------------------------------------------------------
# H7 y H4: convivencia
# -----------------------------------------------------------------------------
def test_no_deja_mutadas_las_globales_de_sshtunnel(tunnel_env):
    """
    H7: `sshtunnel.create_logger()` toca `logging.captureWarnings`, el logger
    `py.warnings` y el logger global `paramiko.transport`. Nada de eso puede quedar
    cambiado para el proceso del host.
    """
    tunnel_env()
    ssh, rs = _cfg()

    paramiko_logger = logging.getLogger("paramiko.transport")
    pywarnings_logger = logging.getLogger("py.warnings")
    antes = (
        list(paramiko_logger.handlers),
        paramiko_logger.level,
        paramiko_logger.propagate,
        list(pywarnings_logger.handlers),
        warnings.showwarning,
    )

    with open_tunnel(ssh, rs):
        pass

    despues = (
        list(paramiko_logger.handlers),
        paramiko_logger.level,
        paramiko_logger.propagate,
        list(pywarnings_logger.handlers),
        warnings.showwarning,
    )
    assert antes == despues


def test_no_toca_el_root_logger(tunnel_env):
    """H4: level y handlers del root logger identicos antes y despues de operar."""
    tunnel_env()
    ssh, rs = _cfg()

    root = logging.getLogger()
    antes = (root.level, list(root.handlers))

    with open_tunnel(ssh, rs):
        pass

    assert (root.level, list(root.handlers)) == antes


def test_solo_cierra_lo_que_abrio(tunnel_env):
    """H6: un servidor ajeno en otro puerto sigue vivo despues de cerrar el tunel."""
    ajeno = FakePostgres()
    puerto_ajeno = ajeno.start()
    try:
        tunnel_env()
        ssh, rs = _cfg()
        with open_tunnel(ssh, rs):
            pass
        assert probe_redshift(puerto_ajeno), "se cerro algo que no era nuestro"
    finally:
        ajeno.stop()


def test_sin_fuga_de_puertos_ni_hilos_en_20_ciclos(tunnel_env):
    tunnel_env()
    ssh, rs = _cfg()
    hilos_antes = threading.active_count()
    puertos = []

    for _ in range(20):
        with open_tunnel(ssh, rs) as forwarder:
            puertos.append(forwarder.local_bind_port)

    # Se espera a que los hilos bajen en vez de asumir que ya bajaron: paramiko cierra
    # los suyos de forma asincrona y en una maquina cargada tarda mas. El limite se
    # mantiene estricto; lo que se tolera es la lentitud, no la fuga.
    for _ in range(50):
        if threading.active_count() - hilos_antes <= 5:
            break
        time.sleep(0.2)

    hilos_despues = threading.active_count()
    assert hilos_despues - hilos_antes <= 5, (
        f"posible fuga de hilos: {hilos_antes} -> {hilos_despues}"
    )
    ocupados = [puerto for puerto in set(puertos) if not port_is_free(puerto)]
    assert not ocupados, f"puertos sin liberar: {ocupados}"
