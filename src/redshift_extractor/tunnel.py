"""
Tunel SSH endurecido hacia el cluster de Redshift.

Por D1 el tunel se queda en esta libreria y no se comparte: lo que se porta de
`postgres_local_client` es el nivel de endurecimiento, no el archivo. DE-4 fijo el
alcance exacto:

  se porta      I1 (host key), I4 (cierre garantizado), I5 (health check de
                protocolo), I6 (errores tipados), H7 (restore de las mutaciones
                globales de sshtunnel) y el rodeo del deadlock de sshtunnel en
                fallo de autenticacion.
  NO se porta   I3 (reuso por destino) ni I7 (tunnel_status / close_all_tunnels
                publicos): esta libreria abre un tunel por proceso, asi que el
                reuso resuelve un problema que no tiene.

El registro interno de tuneles abiertos existe solo para I4 —cerrar al salir lo que
se abrio— no para reusar.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import hmac
import logging
import os
import signal
import socket
import struct
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Iterator, List, Optional

import paramiko  # type: ignore[import-untyped]
from sshtunnel import SSHTunnelForwarder

from redshift_extractor.errors import (
    TunnelAuthError,
    TunnelBindError,
    TunnelError,
    TunnelHostKeyError,
    TunnelNetworkError,
)
from redshift_extractor.events import OnEvent, emit
from redshift_extractor.logging import get_logger, get_ssh_logger
from redshift_extractor.types import RedshiftConfig, SSHConfig, TunnelInfo

_LOCALHOST = "127.0.0.1"
_PROBE_TIMEOUT_S = 3.0

# Mensaje SSLRequest del protocolo de PostgreSQL: longitud 8 + codigo 80877103.
# Redshift habla el mismo protocolo de frontend, asi que sirve para verificar que del
# otro lado contesta un servidor y no un puerto cualquiera, sin necesidad de
# credenciales.
_PG_SSL_REQUEST = struct.pack("!ii", 8, 80877103)
_PG_SSL_REPLIES = (b"S", b"N", b"E")

_log = get_logger("tunnel")
_lock = threading.RLock()
_opened: List[TunnelInfo] = []
_cleanup_registered = False


# -----------------------------------------------------------------------------
# Verificacion de estado (I5)
# -----------------------------------------------------------------------------
def probe_redshift(
    local_port: int, *, host: str = _LOCALHOST, timeout_s: float = _PROBE_TIMEOUT_S
) -> bool:
    """
    Verificacion real de que el tunel esta vivo.

    Hace handshake TCP contra el puerto local y ademas exige respuesta del servidor
    del otro lado. Que el proceso SSH exista no basta: el caso comun de falla es un
    tunel zombie cuya sesion SSH ya murio del otro lado, con el socket local todavia
    escuchando. Tambien atrapa el tunel abierto contra el puerto equivocado, que hoy
    fallaba mas tarde y con un error de psycopg2.
    """
    if not local_port:
        return False
    try:
        with socket.create_connection((host, local_port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(_PG_SSL_REQUEST)
            reply = sock.recv(1)
    except OSError:
        return False
    return reply in _PG_SSL_REPLIES


def wait_until_alive(
    local_port: int, *, timeout_s: float, probe_timeout_s: float = _PROBE_TIMEOUT_S
) -> bool:
    """
    Insiste con el health check hasta `timeout_s` antes de darlo por muerto.

    Un solo intento no sirve: el forwarder local ya esta escuchando cuando `start()`
    regresa, pero el canal `direct-tcpip` contra el cluster puede tardar mas que el
    timeout de un probe en un enlace lento. Sin este margen, un tunel valido pero
    lento se abortaria con el mensaje de "puerto equivocado", que es peor que el
    problema que el health check resuelve.

    El presupuesto es `SSH_CONNECT_TIMEOUT_S`, el mismo que gobierna el resto de la
    apertura.
    """
    deadline = time.monotonic() + max(timeout_s, probe_timeout_s)
    while True:
        restante = deadline - time.monotonic()
        if probe_redshift(local_port, timeout_s=min(probe_timeout_s, max(0.5, restante))):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def port_is_free(port: int, *, host: str = _LOCALHOST) -> bool:
    if not port:
        return True
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


# -----------------------------------------------------------------------------
# Aislamiento de los efectos secundarios de sshtunnel (H7)
# -----------------------------------------------------------------------------
@contextmanager
def _no_logging_side_effects() -> Iterator[None]:
    """
    Envuelve toda llamada a sshtunnel para que no deje estado global modificado.

    `sshtunnel.create_logger()` hace tres cosas que afectan al proceso entero:
      1. `logging.captureWarnings(True)`, que redirige el modulo warnings a logging;
      2. agrega handlers al logger `py.warnings`;
      3. asigna handlers al logger global `paramiko.transport`.

    Ninguna es aceptable en una libreria que puede convivir con otra que tambien use
    paramiko, asi que se toma snapshot y se restaura. El restore nunca lanza.
    """
    paramiko_logger = logging.getLogger("paramiko.transport")
    pywarnings_logger = logging.getLogger("py.warnings")
    saved_paramiko_handlers = list(paramiko_logger.handlers)
    saved_paramiko_level = paramiko_logger.level
    saved_paramiko_propagate = paramiko_logger.propagate
    saved_pywarnings_handlers = list(pywarnings_logger.handlers)
    saved_showwarning = warnings.showwarning
    saved_logging_showwarning = getattr(logging, "_warnings_showwarning", None)
    try:
        yield
    finally:
        try:
            paramiko_logger.handlers = saved_paramiko_handlers
            paramiko_logger.level = saved_paramiko_level
            paramiko_logger.propagate = saved_paramiko_propagate
            pywarnings_logger.handlers = saved_pywarnings_handlers
            warnings.showwarning = saved_showwarning
            logging._warnings_showwarning = saved_logging_showwarning  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - restaurar logging jamas debe fallar
            _log.debug("No se pudo restaurar el estado de logging: %s", exc)


# -----------------------------------------------------------------------------
# Host key (I1) y autenticacion
# -----------------------------------------------------------------------------
def fingerprint(key: paramiko.PKey) -> str:
    """Fingerprint en el mismo formato que imprime OpenSSH (SHA256:base64)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _known_hosts_path(ssh: SSHConfig) -> Path:
    if ssh.known_hosts_path:
        return Path(ssh.known_hosts_path).expanduser()
    return Path.home() / ".ssh" / "known_hosts"


def _host_key_names(ssh: SSHConfig) -> List[str]:
    if ssh.port == 22:
        return [ssh.host]
    return [f"[{ssh.host}]:{ssh.port}", ssh.host]


def _network_error(ssh: SSHConfig, exc: BaseException) -> TunnelNetworkError:
    """Traduce un fallo de socket al mensaje accionable que corresponda."""
    if isinstance(exc, socket.gaierror):
        return TunnelNetworkError(
            f"No se pudo resolver el host SSH '{ssh.host}': {exc}. Revisa SSH_HOST."
        )
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TunnelNetworkError(
            f"Timeout al conectar a {ssh.host}:{ssh.port} despues de "
            f"{ssh.connect_timeout_s:g}s. Causas tipicas, en orden de probabilidad:\n"
            "  1. El Security Group de AWS no permite el puerto 22 desde tu IP publica "
            "actual (cambia al reconectar la red o el VPN).\n"
            "  2. El bastion esta apagado.\n"
            "El puerto 22 se gestiona fuera de esta libreria."
        )
    if isinstance(exc, ConnectionRefusedError):
        return TunnelNetworkError(
            f"Conexion rechazada en {ssh.host}:{ssh.port}. El host responde pero no hay un "
            "servidor SSH escuchando ahi: revisa que el servicio 'sshd' del bastion este "
            "arriba y que SSH_PORT sea el correcto."
        )
    return TunnelNetworkError(f"No se pudo alcanzar {ssh.host}:{ssh.port}: {exc}")


def _preflight(ssh: SSHConfig) -> None:
    """
    Comprueba que el puerto SSH sea alcanzable antes de involucrar a sshtunnel.

    Es necesario porque sshtunnel captura `socket.error` y `AuthenticationException`
    internamente y termina lanzando un unico error generico, con lo que se perderia la
    distincion entre "no hay ruta" y "credenciales invalidas" (I6).
    """
    try:
        with socket.create_connection((ssh.host, ssh.port), timeout=ssh.connect_timeout_s):
            return
    except OSError as exc:
        raise _network_error(ssh, exc) from exc


def fetch_remote_host_key(ssh: SSHConfig) -> paramiko.PKey:
    """
    Pide al servidor su host key, sin autenticarse ni abrir tunel.

    Hace falta porque un fingerprint es un hash y `sshtunnel` necesita el objeto de la
    llave. Se trae la llave, se compara su fingerprint contra el configurado, y solo
    entonces se le entrega a sshtunnel para que paramiko la exija en la conexion real.
    """
    # El socket se abre aqui y se le entrega ya conectado a paramiko. Si se le pasara
    # la tupla (host, port), paramiko envuelve cualquier fallo de red en un SSHException
    # generico ("Unable to connect to ...") y se perderia la distincion entre "no hay
    # ruta" y "el protocolo SSH fallo".
    try:
        sock = socket.create_connection((ssh.host, ssh.port), timeout=ssh.connect_timeout_s)
    except OSError as exc:
        raise _network_error(ssh, exc) from exc

    transport: Optional[paramiko.Transport] = None
    try:
        transport = paramiko.Transport(sock)
        with _no_logging_side_effects():
            transport.start_client(timeout=ssh.connect_timeout_s)
            return transport.get_remote_server_key()
    except paramiko.SSHException as exc:
        raise TunnelError(
            f"No se pudo negociar SSH con {ssh.host}:{ssh.port} para leer su host key: {exc}"
        ) from exc
    except OSError as exc:
        raise _network_error(ssh, exc) from exc
    finally:
        # transport.close() cierra el socket; si no hubo transport, se cierra a mano.
        try:
            if transport is not None:
                transport.close()
            else:
                sock.close()
        except Exception:  # noqa: BLE001
            pass


def _verify_host_key_fingerprint(ssh: SSHConfig) -> paramiko.PKey:
    """
    Verifica la host key contra los fingerprints del env.

    Es mas fuerte que el camino de known_hosts: ahi el usuario los agrega con
    `ssh-keyscan`, que es confiar en lo que conteste el host (trust on first use). Un
    fingerprint que viene en el archivo de config lo verifico alguien fuera de banda
    antes de escribirlo.
    """
    key = fetch_remote_host_key(ssh)
    recibido = fingerprint(key)
    if any(hmac.compare_digest(recibido, esperado) for esperado in ssh.host_fingerprints):
        return key

    esperados = "\n".join(f"    {valor}" for valor in ssh.host_fingerprints)
    raise TunnelHostKeyError(
        f"La host key de {ssh.host}:{ssh.port} no coincide con ningun fingerprint de "
        f"SSH_HOST_FINGERPRINT.\n"
        f"  recibido : {recibido} ({key.get_name()})\n"
        f"  esperados:\n{esperados}\n"
        "Si el bastion se recreo, pide el fingerprint nuevo a quien lo administra y "
        "actualiza el env. Si no deberia haber cambiado, no conectes: alguien podria "
        "estar interceptando la conexion.\n"
        "Para ver el fingerprint que presenta el servidor: redshift-extractor fingerprint"
    )


def _load_known_host_key(ssh: SSHConfig) -> paramiko.PKey:
    """
    Carga la host key esperada desde known_hosts.

    La verificacion no se deshabilita nunca: `AutoAddPolicy` esta prohibido por I1. Si
    el host es desconocido se falla con instrucciones para agregarlo, en vez de
    aceptarlo automaticamente.
    """
    path = _known_hosts_path(ssh)
    hint = (
        f"Agrega la host key a {path} y verifica el fingerprint con quien administra el "
        f"bastion antes de confiar en ella:\n"
        f"  ssh-keyscan -p {ssh.port} {ssh.host} >> {path}\n"
        f"o conecta una vez a mano y acepta el fingerprint:\n"
        f"  ssh -p {ssh.port} {ssh.user}@{ssh.host}\n"
        "Tambien puedes fijarlo en el env con SSH_HOST_FINGERPRINT=SHA256:..., que es "
        "mas fuerte porque no confia en lo que conteste el host la primera vez."
    )

    if not path.exists():
        raise TunnelHostKeyError(
            f"No existe el archivo known_hosts: {path}. No se puede verificar la identidad "
            f"del bastion {ssh.host}:{ssh.port}.\n{hint}"
        )

    host_keys = paramiko.HostKeys()
    try:
        host_keys.load(str(path))
    except OSError as exc:
        raise TunnelHostKeyError(f"No se pudo leer {path}: {exc}") from exc

    # paramiko negocia exactamente el tipo de la llave que se le pasa, asi que se
    # prefiere la mas fuerte que este registrada para ese host.
    preference = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "rsa-sha2-512")
    for name in _host_key_names(ssh):
        entry = host_keys.lookup(name)
        if not entry:
            continue
        available = list(entry.keys())
        for keytype in preference:
            if keytype in available:
                return entry[keytype]
        return entry[available[0]]

    raise TunnelHostKeyError(
        f"El bastion {ssh.host}:{ssh.port} no esta en {path}, asi que no se puede "
        f"verificar su identidad.\n{hint}"
    )


def resolve_host_key(ssh: SSHConfig) -> paramiko.PKey:
    """
    Obtiene la host key esperada. La verificacion nunca se deshabilita (I1).

    Si hay `SSH_HOST_FINGERPRINT` se usa eso; si no, `known_hosts`.
    """
    if ssh.host_fingerprints:
        return _verify_host_key_fingerprint(ssh)
    _preflight(ssh)
    return _load_known_host_key(ssh)


def _load_private_key(ssh: SSHConfig) -> paramiko.PKey:
    path = Path(ssh.pkey_path).expanduser()
    if not path.exists():
        raise TunnelAuthError(
            f"La llave privada SSH no existe: {path}. Corrige SSH_PKEY_PATH."
        )

    from_path = getattr(paramiko.PKey, "from_path", None)
    if from_path is not None:
        try:
            return from_path(path)
        except paramiko.PasswordRequiredException as exc:
            raise TunnelAuthError(
                f"La llave {path} esta protegida con passphrase y esta libreria no pide "
                f"passphrase. Usa una llave sin passphrase para el bastion. Detalle: {exc}"
            ) from exc
        except (paramiko.SSHException, ValueError, OSError, TypeError) as exc:
            raise TunnelAuthError(
                f"No se pudo leer la llave privada {path}: {exc}. Verifica que sea la llave "
                "PRIVADA (no el .pub) y que el formato este soportado."
            ) from exc

    # paramiko < 3.2 no tiene PKey.from_path: se prueba cada tipo de llave.
    errors: List[str] = []
    for attr in ("Ed25519Key", "RSAKey", "ECDSAKey"):
        key_class = getattr(paramiko, attr, None)
        if key_class is None:
            continue
        try:
            return key_class.from_private_key_file(str(path))
        except paramiko.PasswordRequiredException as exc:
            raise TunnelAuthError(
                f"La llave {path} esta protegida con passphrase y esta libreria no pide "
                f"passphrase. Detalle: {exc}"
            ) from exc
        except (paramiko.SSHException, ValueError, OSError) as exc:
            errors.append(f"{attr}: {exc}")

    raise TunnelAuthError(
        f"No se pudo leer la llave privada {path} con ningun tipo de llave conocido. "
        f"Detalle: {'; '.join(errors)}"
    )


def _diagnose_open_failure(
    ssh: SSHConfig, host_key: paramiko.PKey, pkey: paramiko.PKey, original: Exception
) -> TunnelError:
    """
    Reintenta el handshake con paramiko directo para clasificar el fallo (I6).

    Solo corre en el camino de error, asi que no cuesta nada en el caso normal, y es
    lo que permite distinguir auth de red de host key en vez de colapsar los tres en
    un generico "no se pudo conectar".

    La host key se compara aqui a mano en vez de dejarsela a `Transport.connect`: ese
    metodo lanza un `SSHException` generico con el texto "Bad host key from server"
    (`BadHostKeyException` solo la lanza `SSHClient`), y comparar cadenas de error es
    fragil. Comparandola directamente, ademas, se pueden reportar los dos fingerprints.
    """
    transport: Optional[paramiko.Transport] = None
    try:
        transport = paramiko.Transport((ssh.host, ssh.port))
        with _no_logging_side_effects():
            # Fija el algoritmo de host key al de la llave esperada; si no, el servidor
            # podria ofrecer otro tipo y la comparacion daria un falso desajuste.
            try:
                transport._preferred_keys = [host_key.get_name()]
            except Exception:  # noqa: BLE001 - si la API interna cambia, se sigue igual
                pass

            transport.start_client(timeout=ssh.connect_timeout_s)
            remote_key = transport.get_remote_server_key()
            if remote_key.asbytes() != host_key.asbytes():
                return TunnelHostKeyError(
                    f"La host key de {ssh.host}:{ssh.port} NO coincide con la registrada en "
                    f"{_known_hosts_path(ssh)}.\n"
                    f"  esperada : {fingerprint(host_key)} ({host_key.get_name()})\n"
                    f"  recibida : {fingerprint(remote_key)} ({remote_key.get_name()})\n"
                    "Puede ser que el bastion se haya recreado o que alguien este "
                    "interceptando la conexion. Verifica el fingerprint con quien lo "
                    "administra y solo entonces reemplaza la entrada en known_hosts."
                )

            transport.auth_publickey(ssh.user, pkey)
    except paramiko.BadHostKeyException:
        return TunnelHostKeyError(
            f"La host key de {ssh.host}:{ssh.port} NO coincide con la registrada en "
            f"{_known_hosts_path(ssh)}. Verifica el fingerprint con quien administra el "
            "bastion y solo entonces reemplaza la entrada en known_hosts."
        )
    except paramiko.AuthenticationException:
        return TunnelAuthError(
            f"Autenticacion SSH rechazada para el usuario '{ssh.user}' en "
            f"{ssh.host}:{ssh.port} usando la llave {ssh.pkey_path}. Revisa SSH_USER y "
            "SSH_PKEY_PATH.\n"
            "En Linux/macOS la llave debe tener permisos restringidos (chmod 400); si el "
            "bastion es Windows Server y la cuenta esta en el grupo Administrators, la "
            "llave publica va en "
            r"C:\ProgramData\ssh\administrators_authorized_keys"
            ", no en ~\\.ssh\\authorized_keys."
        )
    except paramiko.SSHException as exc:
        return TunnelError(f"Fallo el protocolo SSH contra {ssh.host}:{ssh.port}: {exc}")
    except OSError as exc:
        return TunnelNetworkError(f"No se pudo alcanzar {ssh.host}:{ssh.port}: {exc}")
    else:
        return TunnelError(
            f"No se pudo abrir el tunel a {ssh.host}:{ssh.port} y el diagnostico posterior "
            f"si logro autenticarse, asi que probablemente sea intermitente. "
            f"Error original: {original}"
        )
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass


# -----------------------------------------------------------------------------
# Cierre garantizado (I4)
# -----------------------------------------------------------------------------
def _register_cleanup() -> None:
    """
    Registra la limpieza al terminar el proceso.

    `atexit` es aditivo y por lo tanto seguro. El handler de SIGTERM se encadena al
    previo en vez de reemplazarlo. SIGINT no se toca: su comportamiento por default es
    levantar KeyboardInterrupt, que es justo lo que queremos para que atexit corra
    normalmente (H9).
    """
    global _cleanup_registered
    if _cleanup_registered:
        return
    _cleanup_registered = True

    atexit.register(_atexit_cleanup)

    if threading.current_thread() is not threading.main_thread():
        return
    try:
        previous = signal.getsignal(signal.SIGTERM)
    except (ValueError, AttributeError):  # pragma: no cover - plataforma sin SIGTERM
        return

    def _handler(signum: int, frame: Any) -> None:
        _atexit_cleanup()
        if callable(previous):
            previous(signum, frame)
        elif previous == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            try:
                os.kill(os.getpid(), signum)
            except Exception:  # noqa: BLE001 - nunca fallar durante el cierre
                pass

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError, AttributeError):  # pragma: no cover
        pass


def _atexit_cleanup() -> None:
    """Cierra lo que abrimos. Tolerante a fallos: jamas lanza durante el cierre (H9)."""
    try:
        _close_all()
    except Exception as exc:  # noqa: BLE001
        try:
            _log.debug("Fallo la limpieza de tuneles al salir: %s", exc)
        except Exception:  # noqa: BLE001
            pass


def _abort_forwarder(forwarder: SSHTunnelForwarder) -> None:
    """
    Limpia un forwarder cuyo `start()` fallo a medias, sin usar `stop()`.

    En sshtunnel 0.4.0, si `start()` falla por autenticacion, el forward server local
    ya quedo en `_server_list` pero su hilo `serve_forever` nunca arranco. `stop()`
    llama `srv.shutdown()`, que se queda esperando para siempre el evento que solo
    pone ese hilo — y `force=True` no ayuda porque el `shutdown()` es incondicional
    (sshtunnel.py:1463). Es el deadlock que hoy cuelga el proceso con una llave
    vencida, en vez de dar un error. Asi que se cierran los sockets directamente.
    """
    for server in list(getattr(forwarder, "_server_list", None) or []):
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass
    try:
        forwarder._server_list = []
        forwarder.is_alive = False
    except Exception:  # noqa: BLE001
        pass

    transport = getattr(forwarder, "_transport", None)
    if transport is None:
        return
    for method in ("close", "stop_thread"):
        try:
            getattr(transport, method)()
        except Exception:  # noqa: BLE001
            pass


def _shutdown_forwarder(forwarder: Optional[SSHTunnelForwarder]) -> None:
    """Cierra el forwarder por la via normal si arranco bien, o a mano si no."""
    if forwarder is None:
        return
    if getattr(forwarder, "is_alive", False):
        try:
            with _no_logging_side_effects():
                forwarder.stop()
            return
        except Exception as exc:  # noqa: BLE001
            _log.debug("stop() del forwarder fallo, se cierra a mano: %s", exc)
    _abort_forwarder(forwarder)


def _forget(info: TunnelInfo, *, on_event: Optional[OnEvent] = None) -> None:
    """Saca el tunel del registro y lo cierra si es nuestro (H6)."""
    with _lock:
        if info in _opened:
            _opened.remove(info)

    if not info.owned:
        return

    _shutdown_forwarder(info.forwarder)
    emit(
        on_event,
        level="INFO",
        event="TUNNEL_CLOSED",
        message=f"Tunel cerrado (localhost:{info.local_port}).",
        local_port=info.local_port,
        ssh_host=info.ssh_host,
        owned=True,
        elapsed_s=round((dt.now() - info.opened_at).total_seconds(), 3),
    )


def _close_all(*, on_event: Optional[OnEvent] = None) -> None:
    with _lock:
        infos = list(_opened)
    for info in infos:
        _forget(info, on_event=on_event)


# -----------------------------------------------------------------------------
# Apertura
# -----------------------------------------------------------------------------
def _open_forwarder(
    ssh: SSHConfig, remote_host: str, remote_port: int, local_port: int
) -> SSHTunnelForwarder:
    host_key = resolve_host_key(ssh)
    pkey = _load_private_key(ssh)

    forwarder: Optional[SSHTunnelForwarder] = None
    try:
        with _no_logging_side_effects():
            forwarder = SSHTunnelForwarder(
                (ssh.host, ssh.port),
                ssh_username=ssh.user,
                ssh_pkey=pkey,
                # I1: la host key se exige, no se agrega sola. AutoAddPolicy prohibido.
                ssh_host_key=host_key,
                remote_bind_address=(remote_host, remote_port),
                local_bind_address=(_LOCALHOST, local_port),
                set_keepalive=ssh.keepalive_s,
                compression=ssh.compression,
                logger=get_ssh_logger(),
                # Deterministico: no leer ~/.ssh/config ni probar llaves al azar del
                # agente o de ~/.ssh. Solo lo que dice el env propio.
                ssh_config_file=None,
                allow_agent=False,
                host_pkey_directories=[],
            )
            forwarder.start()
    except Exception as exc:  # noqa: BLE001 - se reclasifica abajo
        _shutdown_forwarder(forwarder)
        if local_port and not port_is_free(local_port):
            raise TunnelBindError(
                f"El puerto local {local_port} esta ocupado. Libera el puerto o deja "
                "SSH_LOCAL_PORT=0 para que se asigne uno libre automaticamente."
            ) from exc
        raise _diagnose_open_failure(ssh, host_key, pkey, exc) from exc

    return forwarder


@contextmanager
def open_tunnel(
    ssh: SSHConfig,
    redshift: RedshiftConfig,
    *,
    on_event: Optional[OnEvent] = None,
) -> Iterator[SSHTunnelForwarder]:
    """
    Abre un tunel SSH hacia el host de Redshift y expone un puerto local.

    Sigue devolviendo el `SSHTunnelForwarder` para no romper a quien lea
    `tunnel.local_bind_port` (E8). El tunel se cierra al salir del bloque, ante
    excepcion, y tambien si el proceso muere por `Ctrl+C` o `SIGTERM` (I4).
    """
    started = dt.now()
    local_port = ssh.local_port

    if local_port and not port_is_free(local_port):
        raise TunnelBindError(
            f"El puerto local {local_port} esta ocupado por otro proceso. No se reusa a "
            "ciegas: si fuera otro tunel, la conexion funcionaria pero apuntaria al "
            "cluster equivocado. Libera el puerto o deja SSH_LOCAL_PORT=0."
        )

    emit(
        on_event,
        level="INFO",
        event="TUNNEL_START",
        message=f"Abriendo tunel SSH a {ssh.host}:{ssh.port} -> {redshift.host}:{redshift.port}.",
        ssh_host=ssh.host,
        ssh_user=ssh.user,
        local_port=local_port or "efimero",
        redshift_host=redshift.host,
        redshift_port=redshift.port,
    )

    forwarder = _open_forwarder(ssh, redshift.host, redshift.port, local_port)
    info = TunnelInfo(
        local_port=int(forwarder.local_bind_port),
        remote_host=redshift.host,
        remote_port=redshift.port,
        ssh_host=ssh.host,
        ssh_port=ssh.port,
        ssh_user=ssh.user,
        opened_at=dt.now(),
        owned=True,
        forwarder=forwarder,
    )
    with _lock:
        _opened.append(info)
    _register_cleanup()

    if not wait_until_alive(info.local_port, timeout_s=ssh.connect_timeout_s):
        _forget(info, on_event=on_event)
        raise TunnelError(
            f"El tunel a {redshift.host}:{redshift.port} quedo abierto en "
            f"localhost:{info.local_port} pero del otro lado no contesta un servidor "
            f"Redshift despues de {ssh.connect_timeout_s:g}s.\n"
            "Causas tipicas, en orden de probabilidad:\n"
            f"  1. El Security Group del cluster no permite el puerto {redshift.port} "
            "desde el bastion.\n"
            f"  2. El HOST o el PORT del alias estan mal: el tunel se abre igual contra "
            "un destino equivocado.\n"
            "  3. El cluster esta pausado.\n"
            "Sin esta verificacion el error apareceria mas tarde y como un timeout de "
            "psycopg2, que apunta al lugar equivocado. Si el enlace es muy lento, sube "
            "SSH_CONNECT_TIMEOUT_S."
        )

    emit(
        on_event,
        level="INFO",
        event="TUNNEL_READY",
        message=f"Tunel listo en localhost:{info.local_port}.",
        local_port=info.local_port,
        ssh_host=ssh.host,
        owned=True,
        elapsed_s=round((dt.now() - started).total_seconds(), 3),
    )

    try:
        yield forwarder
    finally:
        _forget(info, on_event=on_event)


def _reset_for_tests() -> None:
    """Limpia el registro sin cerrar nada. Uso exclusivo de la suite de tests."""
    with _lock:
        _opened.clear()


__all__ = [
    "fetch_remote_host_key",
    "fingerprint",
    "open_tunnel",
    "port_is_free",
    "probe_redshift",
    "resolve_host_key",
    "wait_until_alive",
]
