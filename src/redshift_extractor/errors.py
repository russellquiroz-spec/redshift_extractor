"""
Jerarquia de errores propia (F1).

Divergencia deliberada de la referencia, por E8: en `postgres_local_client` la raiz
hereda de `Exception`, pero esta libreria es la mas importada del ecosistema y los
hosts ya tienen `except RuntimeError` y `except ValueError` alrededor de sus
llamadas, porque es lo que se lanzaba antes de que existiera este modulo. La raiz
hereda de `RuntimeError` y `ConfigError` tambien de `ValueError` para que ese codigo
siga atrapando lo mismo. Se puede estrechar cuando los hosts conocidos migren a
`RedshiftExtractorError`.
"""

from __future__ import annotations


class RedshiftExtractorError(RuntimeError):
    """Base de todos los errores de la libreria."""


class ConfigError(RedshiftExtractorError, ValueError):
    """Configuracion ausente, incompleta o invalida. CLI: exit code 2."""


class EnvFileNotFoundError(ConfigError, FileNotFoundError):
    """No se encontro el `.env.redshift_extractor`, o la ruta declarada no existe."""


class TunnelError(RedshiftExtractorError):
    """Base de los errores de tunel. CLI: exit code 3."""


class TunnelNetworkError(TunnelError):
    """No hay ruta al puerto SSH: Security Group, IP local cambiada o bastion apagado."""


class TunnelAuthError(TunnelError):
    """Llave SSH invalida, vencida o inaccesible."""


class TunnelHostKeyError(TunnelError):
    """La host key del bastion no esta en known_hosts o no coincide con la esperada."""


class TunnelBindError(TunnelError):
    """El puerto local pedido esta ocupado por algo que no es un tunel valido."""


class QueryError(RedshiftExtractorError):
    """El cluster rechazo la conexion o la consulta. Envuelve a psycopg2 (F2)."""


__all__ = [
    "ConfigError",
    "EnvFileNotFoundError",
    "QueryError",
    "RedshiftExtractorError",
    "TunnelAuthError",
    "TunnelBindError",
    "TunnelError",
    "TunnelHostKeyError",
    "TunnelNetworkError",
]
