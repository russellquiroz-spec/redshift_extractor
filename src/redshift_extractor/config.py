from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from dotenv import dotenv_values

import redshift_extractor.secret_loader as _secret_loader
from redshift_extractor.errors import ConfigError, EnvFileNotFoundError
from redshift_extractor.events import OnEvent, emit, register_secret
from redshift_extractor.types import AppConfig, RedshiftConfig, SSHConfig

ENV_FILE_NAME = ".env.redshift_extractor"
ENV_FILE_OVERRIDE_VAR = "REDSHIFT_EXTRACTOR_ENV_FILE"

_SEARCH_DEPTH = 8
_BOM = b"\xef\xbb\xbf"

_REDSHIFT_KEY_RE = re.compile(r"^REDSHIFT__(?P<alias>[A-Za-z0-9_-]+)__(?P<field>[A-Z_]+)$")
_REQUIRED_RS_FIELDS = {"HOST", "PORT", "DBNAME"}
_CREDENTIAL_ENV_FIELD = "CREDENTIALS_ENV"
_get_env_value = _secret_loader.read_system_env_value
_read_windows_env_from_registry = _secret_loader.read_windows_env_value_from_registry


# -----------------------------------------------------------------------------
# Localizacion y lectura del env propio
# -----------------------------------------------------------------------------
def find_env_file() -> Path:
    """
    Encuentra .env.redshift_extractor sin depender del cwd del notebook.

    Orden:
    1) REDSHIFT_EXTRACTOR_ENV_FILE (si esta seteado)
    2) Busca hacia arriba desde el directorio del paquete hasta 8 niveles
       (cubre editable installs: <repo>/src/redshift_extractor/*.py)
    """
    override = os.environ.get(ENV_FILE_OVERRIDE_VAR)
    if override and override.strip():
        path = Path(override.strip()).expanduser().resolve()
        if not path.exists():
            raise EnvFileNotFoundError(
                f"{ENV_FILE_OVERRIDE_VAR} apunta a un archivo inexistente: {path}"
            )
        return path

    searched = []
    current = Path(__file__).resolve().parent
    for _ in range(_SEARCH_DEPTH):
        candidate = current / ENV_FILE_NAME
        searched.append(candidate)
        if candidate.exists():
            return candidate
        if current.parent == current:
            break
        current = current.parent

    rendered = "\n".join(f"  - {path}" for path in searched)
    raise EnvFileNotFoundError(
        f"No se encontro {ENV_FILE_NAME}. Se intentaron las dos rutas de resolucion:\n"
        f"1) La variable {ENV_FILE_OVERRIDE_VAR} (no esta definida o esta vacia).\n"
        f"2) Busqueda hacia arriba desde el paquete instalado:\n{rendered}\n"
        f"Copia .env.example a {ENV_FILE_NAME} en la raiz del repo, o define "
        f"{ENV_FILE_OVERRIDE_VAR} con una ruta absoluta."
    )


def read_env_file(path: Path) -> Dict[str, str]:
    """
    Lee el archivo con `dotenv_values`, que devuelve un dict y NO toca os.environ.

    El env nunca se carga al entorno del proceso, que es lo que hacia la otra funcion
    de python-dotenv. Eso rompe la convivencia con las hermanas del ecosistema: si un
    proyecto host instala dos y ambas definen una variable con el mismo nombre plano
    (SSH_HOST, SSH_PORT, SSH_USER, SSH_PKEY_PATH, LOG_LEVEL, OUTPUT_DIR), la primera en
    cargar gana —python-dotenv usa override=False por defecto— y la segunda se queda
    en silencio con los valores de la otra.

    El BOM se rechaza con mensaje explicito en vez de tolerarse con `utf-8-sig`
    (DE-1, cerrada): aceptarlo en silencio contradice C7 y F5, y el sintoma que
    produce —"la primera variable se lee vacia"— es de los mas caros de diagnosticar.
    """
    raw = path.read_bytes()
    if raw.startswith(_BOM):
        raise ConfigError(
            f"El archivo {path} empieza con BOM (bytes EF BB BF).\n"
            "python-dotenv no lo maneja: la PRIMERA variable del archivo se leeria vacia.\n"
            "Guardalo en UTF-8 SIN BOM. PowerShell 5.1 (Set-Content, >, Out-File) agrega BOM; "
            "usa un editor que permita 'UTF-8 sin BOM' o recrealo con Python:\n"
            '  python -c "import io; p=r\'' + str(path) + "'; "
            "t=io.open(p,encoding='utf-8-sig').read(); "
            'io.open(p,\'w\',encoding=\'utf-8\',newline=\'\').write(t)"'
        )

    values = dotenv_values(dotenv_path=path, encoding="utf-8")
    return {key: value for key, value in values.items() if value is not None}


def read_own_env() -> Dict[str, str]:
    """Lee el env propio y devuelve un dict, SIN escribir en os.environ (C2)."""
    return read_env_file(find_env_file())


def _as_int(value: Optional[str], *, default: int, what: str) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"{what} no es un entero valido: '{value}'.") from exc


def _as_float(value: Optional[str], *, default: float, what: str) -> float:
    if value is None or not str(value).strip():
        return default
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"{what} no es un numero valido: '{value}'.") from exc


def _as_bool(value: Optional[str], *, default: bool, what: str) -> bool:
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ConfigError(
        f"{what} no es un booleano valido: '{value}'. Usa true/false (o 1/0, yes/no, on/off)."
    )


#: Un fingerprint SHA256 con su prefijo, tal como lo imprime OpenSSH.
#: El lookahead final es lo que evita que un token de 44 caracteres matchee sus primeros
#: 43 y se guarde truncado en silencio.
_SHA256_RE = re.compile(r"SHA256:([A-Za-z0-9+/]{43}=*)(?![A-Za-z0-9+/=])")
_MENCION_SHA256_RE = re.compile(r"SHA256:", re.IGNORECASE)


def _as_fingerprints(value: Optional[str]) -> Tuple[str, ...]:
    """
    Normaliza los fingerprints de host key a `SHA256:<base64 sin padding>`.

    Un fingerprint no es un secreto —es el hash de una llave publica— asi que puede
    vivir en el archivo de config. Se aceptan la linea completa de `ssh-keygen -l`,
    varios fingerprints separados por coma o espacio, y el base64 pelado.
    """
    if not value or not value.strip():
        return ()

    texto = value.strip()

    if "MD5:" in texto.upper():
        raise ConfigError(
            "SSH_HOST_FINGERPRINT trae un fingerprint MD5. MD5 esta obsoleto para esto; "
            "usa el SHA256 que imprime 'ssh-keygen -l -f <known_hosts>' o "
            "'redshift-extractor fingerprint'."
        )

    menciones = len(_MENCION_SHA256_RE.findall(texto))
    if menciones:
        encontrados = _SHA256_RE.findall(texto)
        # Si alguna mencion de SHA256: no produjo un fingerprint valido, no se ignora en
        # silencio: descartar callado un fingerprint que el usuario quiso poner deja la
        # verificacion mas debil de lo que el cree.
        if len(encontrados) != menciones:
            raise ConfigError(
                f"SSH_HOST_FINGERPRINT tiene {menciones} entradas 'SHA256:' pero solo "
                f"{len(encontrados)} son validas. Cada una debe ser 'SHA256:' seguido de "
                "exactamente 43 caracteres base64 (letras, digitos, '+' y '/'); si sobra o "
                "falta alguno, suele ser un caracter pegado o un copy/paste cortado. "
                f"Valor recibido: '{texto[:120]}'."
            )
        return tuple(dict.fromkeys(f"SHA256:{base.rstrip('=')}" for base in encontrados))

    fingerprints = []
    for pieza in re.split(r"[,;\s]+", texto):
        if not pieza:
            continue
        limpia = pieza.strip().strip("'\"").rstrip("=")
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}", limpia):
            raise ConfigError(
                f"SSH_HOST_FINGERPRINT no parece un fingerprint SHA256 valido: '{pieza}'. "
                "Se espera 'SHA256:' seguido de 43 caracteres base64, o el base64 solo. "
                "Tambien se acepta pegar tal cual la linea de 'ssh-keygen -l'."
            )
        fingerprints.append(f"SHA256:{limpia}")
    return tuple(dict.fromkeys(fingerprints))


def _resolve_rs_credentials(alias: str, fields: Dict[str, str]) -> Tuple[str, str]:
    user = fields.get("USER")
    password = fields.get("PASSWORD")
    credentials_env = fields.get(_CREDENTIAL_ENV_FIELD)

    if credentials_env:
        try:
            user, password = _secret_loader.resolve_secret_reference(credentials_env.strip())
        except (ValueError, RuntimeError) as exc:
            raise ConfigError(f"REDSHIFT__{alias}__{_CREDENTIAL_ENV_FIELD}: {exc}") from exc

    missing = [name for name, value in (("USER", user), ("PASSWORD", password)) if not value]
    if missing:
        raise ConfigError(
            f"Config Redshift incompleta para alias '{alias}'. Faltan: {missing}. "
            f"Define USER/PASSWORD o {_CREDENTIAL_ENV_FIELD}."
        )

    register_secret(password)
    return str(user), str(password)


def _build_app_config(values: Mapping[str, str]) -> AppConfig:
    from redshift_extractor.logging import resolve_level

    default_alias = values.get("DEFAULT_ALIAS")
    return AppConfig(
        log_level=resolve_level(None, dict(values)),
        output_dir=values.get("OUTPUT_DIR") or "./output",
        default_alias=(default_alias or "").strip().lower() or None,
    )


def _build_ssh_config(values: Mapping[str, str]) -> SSHConfig:
    ssh_host = values.get("SSH_HOST")
    ssh_user = values.get("SSH_USER")
    ssh_pkey_path = values.get("SSH_PKEY_PATH")

    missing = [
        key
        for key, value in (
            ("SSH_HOST", ssh_host),
            ("SSH_USER", ssh_user),
            ("SSH_PKEY_PATH", ssh_pkey_path),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"Faltan variables SSH en {ENV_FILE_NAME}: {missing}")

    return SSHConfig(
        host=str(ssh_host),
        port=_as_int(values.get("SSH_PORT"), default=22, what="SSH_PORT"),
        user=str(ssh_user),
        pkey_path=str(ssh_pkey_path),
        local_port=_as_int(values.get("SSH_LOCAL_PORT"), default=0, what="SSH_LOCAL_PORT"),
        connect_timeout_s=_as_float(
            values.get("SSH_CONNECT_TIMEOUT_S"), default=15.0, what="SSH_CONNECT_TIMEOUT_S"
        ),
        keepalive_s=_as_float(
            values.get("SSH_KEEPALIVE_S"), default=30.0, what="SSH_KEEPALIVE_S"
        ),
        known_hosts_path=values.get("SSH_KNOWN_HOSTS_PATH") or None,
        host_fingerprints=_as_fingerprints(values.get("SSH_HOST_FINGERPRINT")),
        compression=_as_bool(
            values.get("SSH_COMPRESSION"), default=False, what="SSH_COMPRESSION"
        ),
    )


def _build_rs_map(values: Mapping[str, str]) -> Dict[str, RedshiftConfig]:
    buckets: Dict[str, Dict[str, str]] = {}
    for key, value in values.items():
        match = _REDSHIFT_KEY_RE.match(key)
        if not match:
            # Una clave que empieza por REDSHIFT__ y no calza con el patron no se ignora
            # en silencio: el caso tipico es el campo en minusculas
            # (REDSHIFT__prod__host), donde el usuario cree que configuro el host y en
            # realidad no configuro nada (C7).
            if key.startswith("REDSHIFT__"):
                raise ConfigError(
                    f"La clave '{key}' parece un alias pero no tiene la forma esperada "
                    "REDSHIFT__<alias>__<CAMPO>. El alias admite letras, digitos, '_' y "
                    "'-'; el campo va en MAYUSCULAS (HOST, PORT, DBNAME, ...)."
                )
            continue
        alias = match.group("alias").lower()
        buckets.setdefault(alias, {})[match.group("field")] = value

    if not buckets:
        raise ConfigError(
            f"No se encontraron variables REDSHIFT__<alias>__* en {ENV_FILE_NAME}"
        )

    rs_map: Dict[str, RedshiftConfig] = {}
    for alias, fields in buckets.items():
        missing_rs = _REQUIRED_RS_FIELDS - set(fields.keys())
        if missing_rs:
            raise ConfigError(
                f"Config Redshift incompleta para alias '{alias}'. Faltan: {sorted(missing_rs)}"
            )

        user, password = _resolve_rs_credentials(alias, fields)
        rs_map[alias] = RedshiftConfig(
            host=str(fields["HOST"]),
            port=_as_int(fields["PORT"], default=5439, what=f"REDSHIFT__{alias}__PORT"),
            dbname=str(fields["DBNAME"]),
            user=user,
            password=password,
        )
    return rs_map


# -----------------------------------------------------------------------------
# API del modulo
# -----------------------------------------------------------------------------
def load_config() -> Tuple[SSHConfig, Dict[str, RedshiftConfig]]:
    """
    Carga unicamente configuracion desde .env.redshift_extractor.
    No carga .env del proyecto host y no escribe en os.environ.

    Conserva la tupla de dos elementos de siempre: cambiar su aridad romperia todo
    `ssh, rs_map = load_config()` que haya en los hosts (E8). Lo nuevo vive en
    `load_full_config()`.
    """
    _app, ssh, rs_map = load_full_config()
    return ssh, rs_map


def load_full_config(
    *, on_event: Optional[OnEvent] = None
) -> Tuple[AppConfig, SSHConfig, Dict[str, RedshiftConfig]]:
    """Igual que `load_config()` pero incluye el `AppConfig` (log level, DEFAULT_ALIAS)."""
    env_path = find_env_file()
    try:
        values = read_env_file(env_path)
    except ConfigError as exc:
        emit(on_event, level="ERROR", event="ERROR", message=str(exc), path=str(env_path))
        raise

    app = _build_app_config(values)
    ssh = _build_ssh_config(values)
    rs_map = _build_rs_map(values)

    emit(
        on_event,
        level="INFO",
        event="CONFIG_LOADED",
        message="Config loaded.",
        path=str(env_path),
        aliases=len(rs_map),
        ssh_host=ssh.host,
        ssh_port=ssh.port,
        default_alias=app.default_alias,
    )
    return app, ssh, rs_map


def select_alias(
    alias: Optional[str], app: AppConfig, rs_map: Mapping[str, RedshiftConfig]
) -> Tuple[str, RedshiftConfig]:
    requested = (alias or app.default_alias or "").strip().lower()
    available = sorted(rs_map)

    if not requested:
        raise ConfigError(
            f"No se indico alias y DEFAULT_ALIAS no esta definido en {ENV_FILE_NAME}. "
            f"Pasa alias='<alias>' o define DEFAULT_ALIAS. "
            f"Aliases disponibles: {', '.join(available)}."
        )
    if requested not in rs_map:
        raise ConfigError(
            f"El alias '{requested}' no existe. Disponibles: {', '.join(available)}."
        )
    return requested, rs_map[requested]


def resolve(
    alias: Optional[str] = None,
    *,
    on_event: Optional[OnEvent] = None,
) -> Tuple[AppConfig, SSHConfig, str, RedshiftConfig]:
    """Atajo: carga config y resuelve el alias pedido (o DEFAULT_ALIAS)."""
    app, ssh, rs_map = load_full_config(on_event=on_event)
    resolved, cfg = select_alias(alias, app, rs_map)
    return app, ssh, resolved, cfg


__all__ = [
    "ENV_FILE_NAME",
    "ENV_FILE_OVERRIDE_VAR",
    "find_env_file",
    "load_config",
    "load_full_config",
    "read_env_file",
    "read_own_env",
    "resolve",
    "select_alias",
]
