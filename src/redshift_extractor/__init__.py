"""
redshift_extractor: libreria interna para extraer data desde Amazon Redshift via
tunel SSH.

    from redshift_extractor import extract_sql

    df = extract_sql("select 1 as test;")

Principios que importan al usarla:
  - `alias` es keyword-only y por default toma `DEFAULT_ALIAS` del env propio. Las
    formas de 0.1.0 (`db=`, el alias como primer posicional, `list_databases()`) se
    retiraron en 0.3.0.
  - Solo lee `.env.redshift_extractor`; nunca el `.env` del proyecto host, y nunca
    escribe en `os.environ`.
  - El tunel es transparente: la operacion lo abre, verifica la host key del bastion
    y lo cierra siempre, incluso si el proceso muere.
  - Siempre devuelve el DataFrame; guardar a disco es un efecto secundario opcional.

API publica:
- list_aliases() -> List[str]
- extract_sql(query=None, *, alias=None, query_file=None, ...) -> pandas.DataFrame
  (query tiene prioridad sobre query_file)
- ping(alias=None) -> Dict[str, Any]
"""

from redshift_extractor.errors import (
    ConfigError,
    EnvFileNotFoundError,
    QueryError,
    RedshiftExtractorError,
    TunnelAuthError,
    TunnelBindError,
    TunnelError,
    TunnelHostKeyError,
    TunnelNetworkError,
)
from redshift_extractor.events import OnEvent, StatusEvent
from redshift_extractor.extractor import extract_sql, list_aliases, ping
from redshift_extractor.types import AppConfig, RedshiftConfig, SSHConfig, TunnelInfo

__all__ = [
    # lectura
    "extract_sql",
    "list_aliases",
    "ping",
    # contratos
    "AppConfig",
    "OnEvent",
    "RedshiftConfig",
    "SSHConfig",
    "StatusEvent",
    "TunnelInfo",
    # errores
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
__version__ = "0.3.0"
