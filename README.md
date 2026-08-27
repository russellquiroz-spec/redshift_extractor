redshift_extractor

Libreria interna y CLI opcional para extraer datos desde Amazon Redshift por medio de un tunel SSH (bastion/jump host). Soporta multiples conexiones por alias, carga un env propio (`.env.redshift_extractor`) y evita depender del `.env` del proyecto host.

Version 0.3.0. Si vienes de 0.1.0, lee "MIGRACION A 0.3.0" antes de actualizar: hay
cambios de firma que **rompen** y no traen forma vieja.

Primera vez con la libreria: `docs/onboarding.md` lleva de cero a `ping` en verde.

--------------------------------------------------------------------------------
MIGRACION A 0.3.0
--------------------------------------------------------------------------------

**Las formas de 0.1.0 se retiraron. Hay que editar las llamadas.** No hay
`DeprecationWarning`: lo que cambio truena.

| Llamada de 0.1.0 | En 0.3.0 | Que pasa si no la cambias |
|---|---|---|
| `extract_sql("prod", "select 1")` | `extract_sql("select 1", alias="prod")` | `TypeError` |
| `extract_sql("prod", query="select 1")` | `extract_sql("select 1", alias="prod")` | `TypeError` |
| `extract_sql(db="prod", query=...)` | `extract_sql(query=..., alias="prod")` | `TypeError` |
| `extract_sql("prod", query_file="q.sql")` | `extract_sql(query_file="q.sql", alias="prod")` | `TypeError` |
| `list_databases()` | `list_aliases()` | `AttributeError` / `ImportError` |
| `--db prod` en el CLI | `--alias prod` | `No such option: --db`, exit 2 |
| `from redshift_extractor.credentials import ...` | `from redshift_extractor.secret_loader import ...` | `ModuleNotFoundError` |

**El unico caso que NO truena y hay que buscar a mano:** `extract_sql("prod")` con un
solo posicional. Antes ese posicional era el alias; ahora es el SQL, asi que la
llamada llega al cluster con `prod` como consulta y falla como error de SQL, no de la
libreria. Si tienes llamadas de un solo posicional, revisalas primero.

Para encontrar todo lo que hay que editar:

```powershell
findstr /s /n /c:"extract_sql(" *.py
findstr /s /n /c:"list_databases" *.py
findstr /s /n /c:"--db" *.ps1 *.bat *.cmd
```

`alias` es el nombre canonico del concepto en las cuatro librerias del ecosistema que
conectan a una fuente. El cambio viene de la decision DE-2 del `ESTANDAR.md`.

La lista completa de la version, con lo que agrega y lo que arregla, esta en
`CHANGELOG.md`.

**Otros dos cambios que rompen:**

- `pyarrow` salio de las dependencias duras y vive en el extra `parquet`. Si guardas
  Parquet, instala `pip install "redshift-extractor[parquet]"`. Si no lo instalas,
  `save_parquet=True` truena con un ImportError que dice el comando exacto.
- La host key del bastion ahora se verifica **siempre**. Un bastion desconocido ya no
  se acepta en silencio: registra su fingerprint con `SSH_HOST_FINGERPRINT` o agregalo
  a `known_hosts`. Ver el paso 4 de `docs/onboarding.md`.

**Nuevo:**

- `DEFAULT_ALIAS` en el env: con eso puesto, `extract_sql("select 1")` funciona sin
  pasar alias.
- `ping()` y `redshift-extractor ping`: verifica la conexion sin lanzar una consulta
  de negocio.
- Jerarquia de errores propia (`ConfigError`, `TunnelAuthError`, ...). Todos heredan de
  `RuntimeError`, asi que un `except RuntimeError` de antes sigue atrapandolos.
- El tunel se cierra tambien si el proceso muere por `Ctrl+C` o `SIGTERM`.

--------------------------------------------------------------------------------
QUE HACE
--------------------------------------------------------------------------------

- Abre un tunel SSH hacia un bastion, verificando su host key.
- Conecta a Redshift usando `psycopg2` via `127.0.0.1:<puerto_del_tunel>`.
- Ejecuta SQL y devuelve un `pandas.DataFrame`.
- Opcionalmente guarda resultados a CSV y/o Parquet sin dejar de devolver el DataFrame.
- Permite definir varias bases o usuarios con aliases, por ejemplo `prod` y `dev`.
- Emite eventos de estado estructurados para que el proyecto host los imprima, registre o muestre en UI.
- Verifica la conexion de punta a punta con `ping()`, sin lanzar una consulta de negocio.

--------------------------------------------------------------------------------
PRINCIPIOS DE DISENO
--------------------------------------------------------------------------------

- Library-first: API limpia para ser llamada desde otros proyectos.
- Env aislado: carga solo `.env.redshift_extractor`.
- Credenciales fuera del repo: el env del extractor guarda configuracion no sensible y apunta a secretos externos.
- Multiples Redshift: seleccion por alias.
- Estado sin acoplamiento: la libreria no configura logging global.
- Fail-fast: errores explicitos y tempranos, tipados por modo de falla.
- Tunel verificado: host key comprobada siempre, health check de protocolo y cierre garantizado.
- Windows-friendly: normaliza aliases a lowercase y puede leer variables persistidas en registro.

--------------------------------------------------------------------------------
INSTALACION
--------------------------------------------------------------------------------

Plug-and-play con el instalador local. Crea el venv, instala el paquete editable con sus dependencias y genera `.env.redshift_extractor` desde el ejemplo si no existe:

```powershell
python install.py
```

Con dependencias de desarrollo:

```powershell
python install.py --dev
```

Luego activa el entorno y verifica:

```powershell
.\.venv\Scripts\activate
redshift-extractor ls
```

Instalacion manual equivalente (si prefieres no usar el instalador):

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .                    # base
pip install -e ".[parquet]"         # + soporte de Parquet (pyarrow)
pip install -e ".[dev,parquet]"     # + pytest, ruff, mypy
```

`pyarrow` es opcional a proposito: son ~40 MB que solo hacen falta para
`save_parquet=True`.

--------------------------------------------------------------------------------
CONFIGURACION: .env.redshift_extractor
--------------------------------------------------------------------------------

El extractor carga configuracion solo desde su env propio, en este orden:

1. `REDSHIFT_EXTRACTOR_ENV_FILE` si esta definida.
2. Busqueda hacia arriba desde el package instalado hasta encontrar `.env.redshift_extractor`.

Importante: nunca carga automaticamente el `.env` del proyecto host, y nunca escribe en `os.environ`.

El archivo debe estar en UTF-8 **sin BOM**. PowerShell 5.1 (`Set-Content`, `>`, `Out-File`) agrega BOM, y con BOM la primera variable del archivo se leeria vacia: la carga falla con un mensaje que trae el comando para arreglarlo.

### SSH (bastion)

```env
SSH_HOST=your.ssh.host
SSH_PORT=22
SSH_USER=ec2-user
SSH_PKEY_PATH=/absolute/path/to/key.pem

# Puerto local del tunel. 0 = efimero (recomendado).
SSH_LOCAL_PORT=0
```

### Host key del bastion

La verificacion no se puede desactivar. Hay dos caminos, y el primero es mas fuerte:

```env
# Recomendado: verificar contra el fingerprint, que alguien verifico fuera de banda.
# Para ver el que presenta el servidor: redshift-extractor fingerprint
SSH_HOST_FINGERPRINT=SHA256:...

# Alternativa: known_hosts. Por default ~/.ssh/known_hosts.
SSH_KNOWN_HOSTS_PATH=/absolute/path/to/known_hosts
```

Se acepta pegar tal cual la linea de `ssh-keygen -l`, varios fingerprints separados por coma, o el base64 pelado. Un fingerprint cortado no se acepta truncado en silencio.

Opcionales del tunel: `SSH_CONNECT_TIMEOUT_S` (15), `SSH_KEEPALIVE_S` (30), `SSH_COMPRESSION` (false).

### App opcional

```env
LOG_LEVEL=INFO
OUTPUT_DIR=./output

# Alias que se usa cuando no se pasa `alias=`.
DEFAULT_ALIAS=prod
```

La libreria no configura logging por si sola: solo agrega un `NullHandler` a su propio logger `redshift_extractor` y nunca toca el root logger. `LOG_LEVEL` se lee unicamente de este archivo; para pisarlo desde el entorno del proceso, la variable lleva prefijo propio: `REDSHIFT_EXTRACTOR_LOG_LEVEL`. Un `LOG_LEVEL` suelto del host no se consume.

`OUTPUT_DIR` es util para flujos locales o CLI; para la API se recomienda pasar `save_dir` explicitamente.

### Redshift por alias

Cada alias necesita `HOST`, `PORT` y `DBNAME`. Para las credenciales hay dos opciones (elige una por alias):

Opcion A (recomendada): apuntar a una variable de sistema con `CREDENTIALS_ENV`.

```env
REDSHIFT__prod__HOST=your-prod-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__prod__PORT=5439
REDSHIFT__prod__DBNAME=analytics
REDSHIFT__prod__CREDENTIALS_ENV=REDSHIFT_PROD_CREDENTIALS
```

`CREDENTIALS_ENV` debe resolver a credenciales con `user` y `password`. Formatos soportados para la variable de sistema:

```text
{"user":"db_user","password":"db_password"}
USER=db_user;PASSWORD=db_password
db_user:db_password
```

Tambien se soportan JSON con campos extra, JSON anidado y JSON escapado o envuelto como string. Si existe `%APPDATA%\KeyringManager\credentials.json`, el extractor intenta resolver primero una entrada cuyo `env_var` coincida con `CREDENTIALS_ENV`.

Opcion B (solo uso local): credenciales inline con `USER`/`PASSWORD`.

```env
REDSHIFT__dev__HOST=your-dev-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__dev__PORT=5439
REDSHIFT__dev__DBNAME=analytics_dev
REDSHIFT__dev__USER=db_user
REDSHIFT__dev__PASSWORD=db_password
```

Omite `CREDENTIALS_ENV` para usar esta opcion. Si `CREDENTIALS_ENV` esta definido, tiene prioridad sobre `USER/PASSWORD`.

Aliases:

- Permiten letras, numeros, `_` y `-`.
- Internamente se normalizan a lowercase para evitar sorpresas en Windows.

--------------------------------------------------------------------------------
USO COMO LIBRERIA
--------------------------------------------------------------------------------

Listar aliases disponibles:

```python
from redshift_extractor import list_aliases

print(list_aliases())
# ['dev', 'prod']
```

Ejecutar SQL - parametros `query` o `query_file`:

```python
from redshift_extractor import extract_sql

# Opcion A: query directo. El alias sale de DEFAULT_ALIAS si no se pasa.
df = extract_sql("select current_date as today;")
print(df.head())

# Con alias explicito
df = extract_sql("select current_date as today;", alias="prod")
print(df.head())

# Opcion B: desde archivo .sql
df = extract_sql(query_file="path/to/query.sql", alias="prod")
print(df.head())

# Si proporciona ambos, query tiene prioridad
df = extract_sql(
    "select 1;",
    alias="prod",
    query_file="path/to/query.sql",  # ignorado
)
# Se ejecuta: "select 1;"
```

Notas sobre `query_file`:
- Archivo debe ser codificacion UTF-8.
- Se lee el contenido completo como SQL.
- Si el archivo no existe, lanza `FileNotFoundError`.
- `query` tiene prioridad sobre `query_file` (si ambos se proporcionan, se usa `query`).

### Rutas: relativas, absolutas y home directory

**Opcion 1: Ruta relativa (respecto al current working directory)**
```python
# Estructura:
# /proyecto/
# +-- scripts/
# |   +-- extract.py
# +-- queries/
#     +-- usuarios.sql

# En extract.py:
df = extract_sql(query_file="queries/usuarios.sql", alias="prod")

# Funciona si ejecutas desde /proyecto:
# $ cd /proyecto && python scripts/extract.py  [ok]

# Falla si ejecutas desde otro directorio:
# $ cd /home/usuario && python /proyecto/scripts/extract.py  [falla]
```

**Opcion 2: Ruta absoluta (recomendado para robustez)**
```python
# Windows:
df = extract_sql(query_file=r"C:\Users\usuario\proyecto\queries\usuarios.sql", alias="prod")

# Linux/macOS:
df = extract_sql(query_file="/home/usuario/proyecto/queries/usuarios.sql", alias="prod")

# Funciona desde cualquier directorio [ok]
```

**Opcion 3: Ruta relativa al script (MEJOR PRACTICA)**
```python
# En extract.py:
from pathlib import Path

script_dir = Path(__file__).parent
query_file = script_dir / ".." / "queries" / "usuarios.sql"

df = extract_sql(query_file=str(query_file), alias="prod")

# Estructura:
# /proyecto/
# +-- scripts/
# |   +-- extract.py  <- __file__ = /proyecto/scripts/extract.py
# |                      script_dir = /proyecto/scripts
# |                      query_file = /proyecto/queries/usuarios.sql
# +-- queries/
#     +-- usuarios.sql

# Funciona desde cualquier directorio [ok]
```

**Opcion 4: Home directory con `~`**
```python
# Expandir ~ automaticamente:
df = extract_sql(query_file="~/proyecto/queries/usuarios.sql", alias="prod")
# En Windows: C:\Users\TuUsuario\proyecto\queries\usuarios.sql
# En Linux:   /home/tuusuario/proyecto/queries/usuarios.sql

# O con ruta construida:
from pathlib import Path
query_file = Path.home() / "proyecto" / "queries" / "usuarios.sql"
df = extract_sql(query_file=str(query_file), alias="prod")
```

**Resumen de preferencia de rutas:**
1. **Para scripts en produccion**: Opcion 3 (relativa al script) - mas portatil
2. **Para uso interactivo (Jupyter, REPL)**: Opcion 2 (absoluta) - mas explicito
3. **Para home directory**: Opcion 4 con `~` - mas conciso

Verificar la conexion sin lanzar una consulta de negocio:

```python
from redshift_extractor import ping

print(ping("prod"))
# {'ok': True, 'alias': 'prod', 'server_version': 'PostgreSQL 8.0.2 on ..., Redshift ...',
#  'database': 'analytics', 'user': 'tu_usuario', 'redshift_host': '...',
#  'redshift_port': 5439, 'tunnel_port': 51234, 'latency_ms': 842.11}
```

`database` y `user` los reporta el servidor, no el archivo de config: es la forma de
detectar que quedaste conectado a un cluster distinto del que creias. No expone
credenciales.

Guardar resultados y devolver DataFrame:

```python
from redshift_extractor import extract_sql

df = extract_sql(
    "select 1 as test;",
    alias="prod",
    save_dir=r"C:\Users\TuUsuario\Documents\salidas_rs",
    base_name="mi_extraccion",
    save_csv=True,
    save_parquet=True,
)
```

Comportamiento:

- Si `save_dir` es `None` o vacio, solo devuelve DataFrame.
- Si `save_csv=True`, guarda `<base_name>.csv`.
- Si `save_parquet=True`, guarda `<base_name>.parquet`.
- Si `base_name` no se especifica, genera `alias_dbname_timestamp`.

Para Parquet hace falta el extra: `pip install "redshift-extractor[parquet]"`. Si no
esta instalado, `save_parquet=True` truena con un mensaje que dice ese comando.

--------------------------------------------------------------------------------
EXTRACCIONES GRANDES
--------------------------------------------------------------------------------

`extract_sql` trae todo el resultado a memoria y **no** tiene parametro de streaming, a
proposito: la forma barata (`chunksize` que igual devuelve un DataFrame) no baja el pico
de memoria, y la que si lo baja tendria que dejar de devolver el DataFrame. La decision
y sus costos estan en `docs/pendientes.md` seccion B.

Cuando un resultado no quepa, el patron es partir la consulta en el llamador. Es mas
simple, se puede guardar cada trozo a disco y descartarlo, y quien conoce el volumen
decide el corte:

```python
import pandas as pd
from redshift_extractor import extract_sql

SQL = """
select *
from ventas
where fecha >= '{desde}' and fecha < '{hasta}'
"""

trozos = []
for inicio in pd.date_range("2026-01-01", "2026-06-01", freq="MS"):
    fin = inicio + pd.offsets.MonthBegin(1)
    trozos.append(
        extract_sql(
            SQL.format(desde=inicio.date(), hasta=fin.date()),
            alias="prod",
        )
    )

df = pd.concat(trozos, ignore_index=True)
```

Si ni partido cabe en memoria, guarda cada trozo en vez de acumularlo:

```python
for inicio in pd.date_range("2026-01-01", "2026-06-01", freq="MS"):
    fin = inicio + pd.offsets.MonthBegin(1)
    extract_sql(
        SQL.format(desde=inicio.date(), hasta=fin.date()),
        alias="prod",
        save_dir="./output",
        base_name=f"ventas_{inicio:%Y%m}",
        save_parquet=True,
    )
```

Cada llamada abre y cierra su propio tunel. Para muchos trozos eso cuesta ~1 s por
iteracion; si llega a molestar, es la senal de reabrir el reuso de tunel (I3), que DE-4
dejo fuera de esta libreria.

--------------------------------------------------------------------------------
ERRORES
--------------------------------------------------------------------------------

Todos los errores de la libreria cuelgan de `RedshiftExtractorError`, que hereda de
`RuntimeError` para que el codigo que ya atrapaba `RuntimeError` siga funcionando.
Ninguna excepcion de paramiko, sshtunnel o psycopg2 llega al usuario sin envolver.

```text
RedshiftExtractorError  (RuntimeError)
|- ConfigError          (+ ValueError)      config ausente, incompleta o invalida
|  '- EnvFileNotFoundError (+ FileNotFoundError)
|- TunnelError                              base del tunel
|  |- TunnelNetworkError                    no hay ruta al puerto SSH
|  |- TunnelAuthError                       la llave no entra
|  |- TunnelHostKeyError                    host key desconocida o distinta
|  '- TunnelBindError                       puerto local ocupado
'- QueryError                               el cluster rechazo conexion o consulta
```

```python
from redshift_extractor import extract_sql, TunnelNetworkError, QueryError

try:
    df = extract_sql("select 1", alias="prod")
except TunnelNetworkError as exc:
    print("no llego al bastion:", exc)     # Security Group, VPN o bastion apagado
except QueryError as exc:
    print("el cluster contesto un error:", exc)
```

El mensaje dice que hacer, no solo que fallo. La tabla completa de sintomas esta en
`docs/onboarding.md`.

--------------------------------------------------------------------------------
EVENTOS DE ESTADO
--------------------------------------------------------------------------------

Puedes pasar `on_event` para recibir eventos con niveles `DEBUG`, `INFO`, `WARNING` y `ERROR`.

Cada evento es un dict con:

- `ts`
- `level`
- `event`
- `message`
- campos extra como `alias`, `rows`, `cols` o `path`

El campo se llamaba `db` hasta 0.1.0 y ahora es `alias`, **sin forma vieja**: es
deliberado, para que nadie se quede copiando el nombre equivocado. Lo mismo con la
clave `"alias"` que devuelve `ping()`.

Los nombres de los eventos (`TUNNEL_START`, `QUERY_OK`, ...) no cambiaron: los hosts
filtran por esas cadenas. El catalogo completo esta en `events.KNOWN_EVENTS`.

Un `on_event` que lanza excepcion no tumba la operacion en curso: se registra en DEBUG
en el logger propio y se sigue adelante.

Ejemplo para consola o Jupyter:

```python
def printer(evt):
    extras = {k: v for k, v in evt.items() if k not in ("ts", "level", "event", "message")}
    print(f'{evt["ts"]} [{evt["level"]}] {evt["event"]}: {evt["message"]} | {extras}')

from redshift_extractor import extract_sql, list_aliases

print(list_aliases(on_event=printer))
df = extract_sql("select 1 as test;", alias="prod", on_event=printer)
```

Ejemplo para logger del host:

```python
import logging

log = logging.getLogger("host")

def to_logger(evt):
    level = evt["level"]
    msg = evt["message"]
    if level == "DEBUG":
        log.debug(msg, extra=evt)
    elif level == "INFO":
        log.info(msg, extra=evt)
    elif level == "WARNING":
        log.warning(msg, extra=evt)
    else:
        log.error(msg, extra=evt)

df = extract_sql("select 1;", alias="prod", on_event=to_logger)
```

--------------------------------------------------------------------------------
CLI
--------------------------------------------------------------------------------

El paquete expone el comando `redshift-extractor`:

```powershell
redshift-extractor ls
redshift-extractor ping --alias prod
redshift-extractor fingerprint --alias prod
redshift-extractor run --alias prod --query "select 1 as test" --out .\output\result.parquet --fmt parquet
```

Formatos soportados por CLI: `csv` y `parquet`.

`--alias` es el nombre canonico. `--db` se retiro en 0.3.0.

Si el env define `DEFAULT_ALIAS`, `--alias` es opcional.

### ping

Primer comando que hay que correr en una maquina nueva. Verifica tunel, host key,
credenciales y cluster de una sola vez, y reporta lo que ve el servidor.

### fingerprint

Muestra el fingerprint de la host key que presenta el bastion, para verificarlo con
quien lo administra y pegarlo en `SSH_HOST_FINGERPRINT`.

### Codigos de salida

| Codigo | Significa |
|---|---|
| 0 | Todo bien |
| 1 | Error de negocio (SQL, argumentos incompatibles) |
| 2 | Error de configuracion |
| 3 | Error de tunel |

### run-file

Ejecuta un archivo `.sql` directamente. Por defecto envuelve el query con `LIMIT 10` para una prueba rapida (solo aplica a `SELECT`/`WITH`); usa `--full` para el query completo.

```powershell
redshift-extractor run-file query.sql --alias prod                       # prueba: LIMIT 10
redshift-extractor run-file query.sql --alias prod --full                # query completo
redshift-extractor run-file query.sql --alias prod --print-sql --dry-run # arma e imprime el SQL, no ejecuta
redshift-extractor run-file query.sql --alias prod --full --output resultado.csv
```

Opciones:

- `--alias`: alias de conexion (opcional si hay `DEFAULT_ALIAS`; ver con `redshift-extractor ls`).
- `--limit`: filas para el modo prueba. Default: 10.
- `--full`: ejecuta el SQL completo, sin envolverlo con `LIMIT`.
- `--retries` / `--retry-wait`: reintentos ante fallos de conexion. Default: 3 intentos, 5s de espera.
- `--print-sql`: imprime el SQL final que se va a ejecutar.
- `--dry-run`: solo arma/imprime el SQL final; no lo ejecuta.
- `--output`: opcional, guarda el resultado en CSV.

Imprime una muestra de las primeras filas del resultado en consola.

--------------------------------------------------------------------------------
ESTRUCTURA DEL PROYECTO
--------------------------------------------------------------------------------

- `config.py`: localiza el env propio, carga SSH y descubre conexiones Redshift por alias.
- `secret_loader.py`: resuelve credenciales desde KeyringManager, variables de sistema
  y registro de Windows. Es el unico lugar donde vive esa logica: el shim
  `credentials.py` se retiro en 0.3.0.
- `types.py`: contratos (`SSHConfig`, `RedshiftConfig`, `AppConfig`, `TunnelInfo`).
- `errors.py`: jerarquia de errores propia.
- `events.py`: `StatusEvent`, `OnEvent`, `emit()` y el catalogo de eventos.
- `logging.py`: logger propio con `NullHandler`; nunca toca el root logger.
- `tunnel.py`: tunel SSH endurecido (host key, health check, cierre garantizado).
- `extractor.py`: API publica (`list_aliases`, `extract_sql`, `ping`) y persistencia opcional.
- `io.py`: utilidades de escritura.
- `cli.py`: entrypoint de CLI.

Documentacion adicional:

- `docs/onboarding.md`: de cero a `ping` en verde, con tabla de sintomas.
- `docs/compatibilidad.md`: politica de dependencias y garantias de convivencia.
- `docs/pendientes.md`: estado contra el estandar del ecosistema.
- `docs/credential_env_migration.md`: migracion del formato de credenciales.

--------------------------------------------------------------------------------
TROUBLESHOOTING
--------------------------------------------------------------------------------

Empieza siempre por `redshift-extractor ping --alias <alias>`: separa en un segundo
el problema de conexion del problema de SQL. La tabla completa de sintomas y su
remedio esta en `docs/onboarding.md`.

- SSH auth falla (`TunnelAuthError`): revisa `SSH_USER`, `SSH_PKEY_PATH` y permisos del `.pem`.
- Host key desconocida o distinta (`TunnelHostKeyError`): registra el fingerprint con
  `redshift-extractor fingerprint` y verificalo antes de confiar en el. Si cambio sin
  aviso, no conectes.
- No llega al bastion (`TunnelNetworkError`): Security Group con tu IP publica actual,
  VPN, o bastion apagado.
- Puerto local ocupado (`TunnelBindError`): deja `SSH_LOCAL_PORT=0`.
- El tunel abre pero no hay cluster del otro lado: revisa `REDSHIFT__<alias>__HOST/PORT`.
- La variable de credenciales no aparece: abre una terminal nueva o valida el valor persistido en Windows.
- Password raro o con escapes: usa JSON o revisa el parseo con `parse_credentials_secret`.
- Alias no existe: revisa con `list_aliases()` y confirma que el alias este en `.env.redshift_extractor`.
- El env se lee vacio en la primera variable: el archivo tiene BOM. La carga ahora
  falla con el comando exacto para arreglarlo.

--------------------------------------------------------------------------------
SEGURIDAD
--------------------------------------------------------------------------------

- No commitear `.env.redshift_extractor`.
- No guardar `USER/PASSWORD` en el env del repo salvo casos locales controlados.
- Usar variables de sistema, KeyringManager o secretos del runtime.
- Mantener privilegios minimos, idealmente read-only.
- La libreria no imprime ni loggea credenciales.

--------------------------------------------------------------------------------
ROADMAP SUGERIDO
--------------------------------------------------------------------------------

- UNLOAD a S3 para grandes volumenes.
- Streaming/chunks para evitar picos de RAM.
- Override de SSH por alias si cambia bastion por entorno.
- Checks de calidad y metricas de operacion.
- Parametro `chunksize` en `extract_sql` para no traer todo a memoria. Ver
  `docs/pendientes.md`.
