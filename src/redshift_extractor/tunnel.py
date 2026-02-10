from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sshtunnel import SSHTunnelForwarder

from redshift_extractor.types import RedshiftConfig, SSHConfig


@contextmanager
def open_tunnel(ssh: SSHConfig, redshift: RedshiftConfig) -> Iterator[SSHTunnelForwarder]:
    """
    Abre túnel SSH hacia el host de Redshift y expone un puerto local (localhost:<local_bind_port>).
    """
    with SSHTunnelForwarder(
        (ssh.host, ssh.port),
        ssh_username=ssh.user,
        ssh_pkey=ssh.pkey_path,
        remote_bind_address=(redshift.host, redshift.port),
    ) as tunnel:
        yield tunnel