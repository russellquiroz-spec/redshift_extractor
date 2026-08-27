from __future__ import annotations

import logging
import os
from typing import Optional

LOGGER_NAME = "redshift_extractor"

#: Override desde el entorno del proceso. Lleva prefijo propio porque un `LOG_LEVEL`
#: suelto pertenece al host o a otra libreria: si esta lo consumiera, exportar
#: LOG_LEVEL para cualquier otra cosa cambiaria el nivel de log de esta (C3).
LOG_LEVEL_ENV_VAR = "REDSHIFT_EXTRACTOR_LOG_LEVEL"

_CLI_HANDLER_FLAG = "_redshift_extractor_cli_handler"


def get_logger(suffix: Optional[str] = None) -> logging.Logger:
    """
    Devuelve el logger propio de la libreria con un NullHandler.

    Nunca toca el root logger ni la configuracion global de logging: un proyecto host
    que importe esta libreria junto a otra debe encontrar su configuracion de logging
    exactamente como la dejo (G2, G3).
    """
    name = LOGGER_NAME if not suffix else f"{LOGGER_NAME}.{suffix}"
    logger = logging.getLogger(name)
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())
    return logger


def get_ssh_logger() -> logging.Logger:
    """
    Logger que se le pasa a sshtunnel/paramiko.

    Lleva NullHandler y `propagate=False` por dos razones:
      1. sshtunnel agrega un StreamHandler a consola si el logger que recibe no tiene
         ninguno; con el NullHandler presente se salta ese paso.
      2. paramiko es verboso y puede loggear detalle de la negociacion de
         autenticacion. Con propagate=False eso no llega al root logger del host (H8).
    """
    logger = get_logger("ssh")
    logger.propagate = False
    return logger


def resolve_level(level: Optional[str] = None, env_values: Optional[dict] = None) -> str:
    """
    Nivel efectivo: argumento explicito > REDSHIFT_EXTRACTOR_LOG_LEVEL > env propio.

    `env_values` es el dict que ya cargo `config._read_own_env()`; el `LOG_LEVEL` del
    archivo propio se lee de ahi, nunca de `os.environ`.
    """
    if level:
        return level.upper()
    from_process = os.environ.get(LOG_LEVEL_ENV_VAR)
    if from_process and from_process.strip():
        return from_process.strip().upper()
    if env_values:
        from_file = env_values.get("LOG_LEVEL")
        if from_file and from_file.strip():
            return from_file.strip().upper()
    return "INFO"


def configure_logging(level: Optional[str] = None) -> None:
    """
    Manda el logger propio a consola. Pensado para la CLI, no para uso como libreria.

    Configura unicamente el logger `redshift_extractor`: el root logger queda intacto,
    porque la funcion de conveniencia del modulo logging que lo configura no se usa en
    ningun modulo de esta libreria (G4, H3). Llamarlo dos veces no duplica handlers.
    """
    env_values = None
    try:
        from redshift_extractor.config import read_own_env

        env_values = read_own_env()
    except Exception:  # noqa: BLE001 - configurar el log no debe depender de la config
        env_values = None

    logger = get_logger()
    for handler in list(logger.handlers):
        if getattr(handler, _CLI_HANDLER_FLAG, False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler()
    setattr(handler, _CLI_HANDLER_FLAG, True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(resolve_level(level, env_values))
    logger.propagate = False


__all__ = [
    "LOGGER_NAME",
    "LOG_LEVEL_ENV_VAR",
    "configure_logging",
    "get_logger",
    "get_ssh_logger",
    "resolve_level",
]
