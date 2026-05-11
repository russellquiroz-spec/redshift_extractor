from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SSHConfig:
    host: str
    port: int
    user: str
    pkey_path: str


@dataclass(frozen=True)
class RedshiftConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "INFO"
    output_dir: str = "./output"
