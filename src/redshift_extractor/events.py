"""
Catalogo de eventos y `emit()` central (G1).

Nombres en MAYUSCULAS a proposito: esta libreria ya emitia `TUNNEL_START`,
`QUERY_OK` y compania desde `extractor.py`, y los hosts filtran por esas cadenas
exactas. Pasarlos a minusculas para igualar a la referencia romperia a los
consumidores sin ganar nada, asi que se conserva el casing y los eventos nuevos
siguen el mismo estilo (E8).

El unico campo que si cambia sin forma vieja es `db=`, que pasa a `alias=`, porque
el contrato del renombre lo pide explicitamente para que nadie copie el nombre
equivocado.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime as dt
from typing import Any, Callable, Dict, Iterable, Optional

from redshift_extractor.logging import get_logger

StatusEvent = Dict[str, Any]
OnEvent = Callable[[StatusEvent], None]

Level = str
EventName = str

#: Eventos del contrato. Se valida en tests; emitir otros no es error.
KNOWN_EVENTS = frozenset(
    {
        "CONFIG_LOADED",
        "ALIAS_RESOLVED",
        "TUNNEL_START",
        "TUNNEL_READY",
        "TUNNEL_CLOSED",
        "DB_CONNECT_START",
        "DB_CONNECTED",
        "QUERY_START",
        "QUERY_OK",
        #: El guardado quedo activado. No es el inicio de una consulta: antes se
        #: emitia como `QUERY_START` y desbalanceaba la cuenta de consultas.
        "SAVE_CONFIGURED",
        "FILE_SAVED",
        "CONNECTION_CLOSED",
        "DONE",
        "ERROR",
    }
)

_LEVEL_TO_LOGGING = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_REDACTED = "***"
_MIN_SECRET_LEN = 4

_log = get_logger()
_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def register_secret(*values: Optional[str]) -> None:
    """
    Registra un valor sensible para que `redact()` lo tache si alguna vez aparece
    en un mensaje.

    Red de seguridad, no la defensa principal: el codigo nunca pone credenciales en
    eventos. Existe porque el mensaje de error de una dependencia podria arrastrarlas
    sin que nos demos cuenta.
    """
    with _secrets_lock:
        for value in values:
            if isinstance(value, str) and len(value.strip()) >= _MIN_SECRET_LEN:
                _secrets.add(value.strip())


def clear_secrets() -> None:
    with _secrets_lock:
        _secrets.clear()


def redact(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    with _secrets_lock:
        secrets: Iterable[str] = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        if secret in value:
            value = value.replace(secret, _REDACTED)
    return value


def emit(
    on_event: Optional[OnEvent],
    *,
    level: Level,
    event: EventName,
    message: str,
    **fields: Any,
) -> None:
    """
    Construye el evento, lo manda al logger propio y al callback del host.

    Un callback que lanza excepcion no tumba la operacion en curso: se registra en
    DEBUG en el logger propio y se sigue adelante.
    """
    payload: StatusEvent = {
        "ts": dt.now().isoformat(timespec="seconds"),
        "level": level,
        "event": event,
        "message": redact(message),
    }
    for key, value in fields.items():
        payload[key] = redact(value)

    extras = {k: v for k, v in payload.items() if k not in ("ts", "level", "event", "message")}
    _log.log(
        _LEVEL_TO_LOGGING.get(level, logging.INFO),
        "%s: %s | %s",
        event,
        payload["message"],
        extras,
    )

    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception as exc:  # noqa: BLE001 - un on_event roto no debe tumbar la operacion
        _log.debug("on_event lanzo %s: %s", type(exc).__name__, exc)


__all__ = [
    "KNOWN_EVENTS",
    "OnEvent",
    "StatusEvent",
    "clear_secrets",
    "emit",
    "redact",
    "register_secret",
]
