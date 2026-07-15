redshift_extractor

Libreria interna y CLI opcional para extraer datos desde Amazon Redshift por medio de un tunel SSH (bastion/jump host). Soporta multiples conexiones por alias, carga un env propio (`.env.redshift_extractor`) y evita depender del `.env` del proyecto host.

--------------------------------------------------------------------------------
QUE HACE
--------------------------------------------------------------------------------

- Abre un tunel SSH hacia un bastion.
- Conecta a Redshift usando `psycopg2` via `localhost:<puerto_del_tunel>`.
- Ejecuta SQL y devuelve un `pandas.DataFrame`.
- Opcionalmente guarda resultados a CSV y/o Parquet sin dejar de devolver el DataFrame.
- Permite definir varias bases o usuarios con aliases, por ejemplo `prod` y `dev`.
- Emite eventos de estado estructurados para que el proyecto host los imprima, registre o muestre en UI.

--------------------------------------------------------------------------------
PRINCIPIOS DE DISENO
--------------------------------------------------------------------------------

- Library-first: API limpia para ser llamada desde otros proyectos.
- Env aislado: carga solo `.env.redshift_extractor`.
- Credenciales fuera del repo: el env del extractor guarda configuracion no sensible y apunta a secretos externos.
- Multiples Redshift: seleccion por alias.
- Estado sin acoplamiento: la libreria no configura logging global.
- Fail-fast: errores explicitos y tempranos.
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
pip install -e .          # o: pip install -e ".[dev]"
```

--------------------------------------------------------------------------------
CONFIGURACION: .env.redshift_extractor
--------------------------------------------------------------------------------

El extractor carga configuracion solo desde su env propio, en este orden:

1. `REDSHIFT_EXTRACTOR_ENV_FILE` si esta definida.
2. Busqueda hacia arriba desde el package instalado hasta encontrar `.env.redshift_extractor`.

Importante: nunca carga automaticamente el `.env` del proyecto host.

### SSH (bastion)

```env
SSH_HOST=your.ssh.host
SSH_PORT=22
SSH_USER=ec2-user
SSH_PKEY_PATH=/absolute/path/to/key.pem
```

### App opcional

```env
LOG_LEVEL=INFO
OUTPUT_DIR=./output
```

La libreria no configura logging por si sola. `OUTPUT_DIR` es util para flujos locales o CLI; para la API se recomienda pasar `save_dir` explicitamente.

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

Listar bases disponibles:

```python
from redshift_extractor import list_databases

print(list_databases())
# ['dev', 'prod']
```

Ejecutar SQL:

```python
from redshift_extractor import extract_sql

df = extract_sql(
    db="prod",
    query="select current_date as today;",
)
print(df.head())
```

Guardar resultados y devolver DataFrame:

```python
from redshift_extractor import extract_sql

df = extract_sql(
    "prod",
    "select 1 as test;",
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

Para Parquet, pandas requiere `pyarrow` o `fastparquet`.

--------------------------------------------------------------------------------
EVENTOS DE ESTADO
--------------------------------------------------------------------------------

Puedes pasar `on_event` para recibir eventos con niveles `DEBUG`, `INFO`, `WARNING` y `ERROR`.

Cada evento es un dict con:

- `ts`
- `level`
- `event`
- `message`
- campos extra como `db`, `rows`, `cols` o `path`

Ejemplo para consola o Jupyter:

```python
def printer(evt):
    extras = {k: v for k, v in evt.items() if k not in ("ts", "level", "event", "message")}
    print(f'{evt["ts"]} [{evt["level"]}] {evt["event"]}: {evt["message"]} | {extras}')

from redshift_extractor import extract_sql, list_databases

print(list_databases(on_event=printer))
df = extract_sql("prod", "select 1 as test;", on_event=printer)
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

df = extract_sql("prod", "select 1;", on_event=to_logger)
```

--------------------------------------------------------------------------------
CLI
--------------------------------------------------------------------------------

El paquete expone el comando `redshift-extractor`:

```powershell
redshift-extractor ls
redshift-extractor run --db prod --query "select 1 as test" --out .\output\result.parquet --fmt parquet
```

Formatos soportados por CLI: `csv` y `parquet`.

--------------------------------------------------------------------------------
ESTRUCTURA DEL PROYECTO
--------------------------------------------------------------------------------

- `config.py`: localiza el env propio, carga SSH y descubre conexiones Redshift por alias.
- `secret_loader.py`: resuelve credenciales desde KeyringManager, variables de sistema y registro de Windows.
- `types.py`: contratos (`SSHConfig`, `RedshiftConfig`, etc.).
- `tunnel.py`: manejo del tunel SSH.
- `extractor.py`: API publica (`list_databases`, `extract_sql`), eventos y persistencia opcional.
- `io.py`: utilidades de escritura.
- `cli.py`: entrypoint de CLI.

--------------------------------------------------------------------------------
TROUBLESHOOTING
--------------------------------------------------------------------------------

- SSH auth falla: revisa `SSH_USER`, `SSH_PKEY_PATH` y permisos del `.pem`.
- No llega a Redshift: verifica security groups, VPC/rutas y `REDSHIFT__<alias>__HOST/PORT`.
- La variable de credenciales no aparece: abre una terminal nueva o valida el valor persistido en Windows.
- Password raro o con escapes: usa JSON o revisa el parseo con `parse_credentials_secret`.
- Alias no existe: revisa con `list_databases()` y confirma que el alias este en `.env.redshift_extractor`.

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
