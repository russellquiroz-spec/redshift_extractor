from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class SSHConfig:
    """
    Parametros del tunel SSH.

    Los cuatro primeros campos conservan nombre, orden y obligatoriedad de siempre;
    los que agrego el endurecimiento del tunel van al final con default, asi que
    `SSHConfig(host=..., port=..., user=..., pkey_path=...)` sigue construyendo.
    """

    host: str
    port: int
    user: str
    pkey_path: str
    #: Puerto local. 0 = efimero, que es el default del ecosistema: dos librerias con
    #: puerto fijo en el mismo proceso colisionan (H5).
    local_port: int = 0
    connect_timeout_s: float = 15.0
    keepalive_s: float = 30.0
    known_hosts_path: Optional[str] = None
    #: Fingerprints SHA256 aceptados para la host key, en formato `SHA256:<base64>`.
    #: Si hay alguno se verifica contra estos y no se usa known_hosts.
    host_fingerprints: Tuple[str, ...] = ()
    compression: bool = False


@dataclass(frozen=True)
class RedshiftConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)

    @property
    def target(self) -> str:
        """Referencia segura para logs y errores: nunca la cadena de conexion completa."""
        return f"{self.host}:{self.port}/{self.dbname}"


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "INFO"
    output_dir: str = "./output"
    #: Alias por default, de `DEFAULT_ALIAS` en el env propio (E2).
    default_alias: Optional[str] = None


@dataclass
class TunnelInfo:
    """
    Estado del tunel. `owned=True` significa que lo abrio esta libreria y por lo
    tanto es la unica que puede cerrarlo (H6).
    """

    local_port: int
    remote_host: str
    remote_port: int
    ssh_host: str
    ssh_user: str
    opened_at: datetime
    owned: bool
    ssh_port: int = 22
    forwarder: Any = field(default=None, repr=False, compare=False)

    @property
    def is_alive(self) -> bool:
        """
        Verificacion real: handshake TCP contra el puerto local mas respuesta del
        servidor del otro lado. Que el proceso SSH exista no basta (I5).
        """
        from redshift_extractor.tunnel import probe_redshift

        return probe_redshift(self.local_port)

    def as_dict(self) -> dict:
        return {
            "local_port": self.local_port,
            "remote_host": self.remote_host,
            "remote_port": self.remote_port,
            "ssh_host": self.ssh_host,
            "ssh_port": self.ssh_port,
            "ssh_user": self.ssh_user,
            "opened_at": self.opened_at.isoformat(timespec="seconds"),
            "owned": self.owned,
            "is_alive": self.is_alive,
        }


__all__ = ["AppConfig", "RedshiftConfig", "SSHConfig", "TunnelInfo"]
