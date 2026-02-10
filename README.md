redshift_extractor

Librería interna (y CLI opcional) para extraer datos desde Amazon Redshift a través de un túnel SSH (bastion/jump host). Soporta múltiples conexiones a Redshift mediante aliases, usa un env propio (.env.redshift_extractor) y no depende del .env del proyecto host.

--------------------------------------------------------------------------------
¿QUÉ HACE?
--------------------------------------------------------------------------------
- Abre un túnel SSH hacia un bastion.
- Conecta a Redshift usando psycopg2 vía localhost:<puerto_del_túnel>.
- Ejecuta SQL y devuelve un pandas.DataFrame (uso como librería).
- Opcional: guarda resultados a CSV y/o Parquet en un directorio definido por el usuario, sin dejar de regresar el DataFrame.
- Permite definir varias bases/usuarios con aliases (ej: data-rabbit-prod, dev).
- Opcional: emite eventos de estado estructurados (niveles DEBUG/INFO/WARNING/ERROR) para que el host los imprima o los lleve a su propio logger/UI.

--------------------------------------------------------------------------------
PRINCIPIOS DE DISEÑO
--------------------------------------------------------------------------------
- Library-first: API limpia para ser llamada desde otros proyectos.
- Env aislado: carga solo su .env.redshift_extractor (no toca el .env del host).
- Múltiples Redshift: selección por alias.
- Estado sin acoplamiento: la librería no configura logging global; opcionalmente emite eventos y el host decide qué hacer con ellos.
- Fail-fast: errores explícitos y tempranos.
- Windows-friendly: normaliza aliases a lowercase para evitar el comportamiento case-insensitive de variables de entorno.

--------------------------------------------------------------------------------
INSTALACIÓN (COMO LIBRERÍA INTERNA)
--------------------------------------------------------------------------------
Editable install (recomendado):

1) Crear venv y activarlo:
   python -m venv .venv

   Windows:
   .venv\Scripts\activate

   macOS/Linux:
   source .venv/bin/activate

2) Instalar como editable desde el repo:
   pip install -e /ruta/al/repo/redshift_extractor

--------------------------------------------------------------------------------
CONFIGURACIÓN — .env.redshift_extractor
--------------------------------------------------------------------------------
El extractor carga configuración solo desde un env propio en este orden:
1) Variable REDSHIFT_EXTRACTOR_ENV_FILE (ruta absoluta o relativa),
2) Búsqueda hacia arriba desde el package instalado (soporta editable installs) hasta encontrar .env.redshift_extractor.

Importante: Nunca carga .env del proyecto host automáticamente.

--------------------
SSH (BASTION)
--------------------
SSH_HOST=your.ssh.host
SSH_PORT=22
SSH_USER=ec2-user
SSH_PKEY_PATH=/absolute/path/to/key.pem

- SSH_PKEY_PATH es la ruta a la llave .pem usada para autenticación SSH.

--------------------
APP (OPCIONALES)
--------------------
LOG_LEVEL=INFO
OUTPUT_DIR=./output

- LOG_LEVEL: si el proyecto host usa logging, puede usar esta variable; la librería no configura logging por sí sola.
- OUTPUT_DIR: útil para flujos/CLI (si aplica). Para la API de librería, se recomienda pasar save_dir explícito.

--------------------
REDSHIFT (MÚLTIPLES CONEXIONES POR ALIAS)
--------------------
Formato:
REDSHIFT__<ALIAS>__HOST=...
REDSHIFT__<ALIAS>__PORT=5439
REDSHIFT__<ALIAS>__DBNAME=...
REDSHIFT__<ALIAS>__USER=...
REDSHIFT__<ALIAS>__PASSWORD=...

Ejemplo:
REDSHIFT__data-rabbit-prod__HOST=your-core-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__data-rabbit-prod__PORT=5439
REDSHIFT__data-rabbit-prod__DBNAME=core_db
REDSHIFT__data-rabbit-prod__USER=core_user
REDSHIFT__data-rabbit-prod__PASSWORD="core_password_with#chars"

REDSHIFT__dev__HOST=your-mkt-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__dev__PORT=5439
REDSHIFT__dev__DBNAME=mkt_db
REDSHIFT__dev__USER=mkt_user
REDSHIFT__dev__PASSWORD="mkt_password"

Passwords:
- Si contiene #, espacios, =, $, !, etc., usa comillas.

Aliases:
- Permiten letras, números, _ y -.
- Internamente se normalizan a lowercase para que el usuario copie/pegue sin sorpresas en Windows.

--------------------------------------------------------------------------------
USO COMO LIBRERÍA (RECOMENDADO)
--------------------------------------------------------------------------------
Listar bases disponibles:
from redshift_extractor import list_databases
print(list_databases())
# ['data-rabbit-prod', 'dev']

Ejecutar SQL y obtener DataFrame:
from redshift_extractor import extract_sql

df = extract_sql(
    db="data-rabbit-prod",
    query="select current_date as today;",
)
print(df.head())

--------------------------------------------------------------------------------
EVENTOS DE ESTADO (NIVELES) — SIN LOGGING GLOBAL
--------------------------------------------------------------------------------
Puedes pasar un callback on_event para recibir eventos con niveles:
- DEBUG, INFO, WARNING, ERROR

Cada evento es un dict con:
- ts (ISO datetime)
- level
- event (tipo)
- message
- campos extra (ej: db, dbname, rows, cols, path, etc.)

Ejemplo: imprimir eventos en consola (Jupyter friendly)
def printer(evt):
    extras = {k: v for k, v in evt.items() if k not in ("ts", "level", "event", "message")}
    print(f'{evt["ts"]} [{evt["level"]}] {evt["event"]}: {evt["message"]} | {extras}')

from redshift_extractor import list_databases, extract_sql
print(list_databases(on_event=printer))
df = extract_sql("data-rabbit-prod", "select 1 as test;", on_event=printer)

Ejemplo: enviar eventos al logger del host (sin que la librería configure nada)
import logging
log = logging.getLogger("host")

def to_logger(evt):
    msg = evt["message"]
    level = evt["level"]
    if level == "DEBUG":
        log.debug(msg, extra=evt)
    elif level == "INFO":
        log.info(msg, extra=evt)
    elif level == "WARNING":
        log.warning(msg, extra=evt)
    else:
        log.error(msg, extra=evt)

df = extract_sql("data-rabbit-prod", "select 1;", on_event=to_logger)

--------------------------------------------------------------------------------
GUARDAR RESULTADOS A CSV Y/O PARQUET (Y TAMBIÉN DEVOLVER DATAFRAME)
--------------------------------------------------------------------------------
La extracción siempre devuelve DataFrame.
Si además quieres persistir a disco, activa save_dir y el/los formatos:

from redshift_extractor import extract_sql

df = extract_sql(
    "data-rabbit-prod",
    "select 1 as test;",
    on_event=printer,                           # opcional
    save_dir=r"C:\Users\TuUsuario\Documents\salidas_rs",
    base_name="mi_extraccion",                  # opcional; si no se da, se genera uno
    save_csv=True,
    save_parquet=True,
)

Comportamiento:
- Si save_dir es None (o vacío) -> solo extrae a DataFrame (no guarda nada).
- Si save_dir está definido:
  - save_csv=True guarda base_name.csv
  - save_parquet=True guarda base_name.parquet
- base_name si no se especifica: se genera con alias_dbname_timestamp.

Requisitos Parquet:
- pandas requiere pyarrow (recomendado) o fastparquet.
- Recomendado: pyarrow>=18.

--------------------------------------------------------------------------------
ESTRUCTURA DEL PROYECTO (MÓDULOS)
--------------------------------------------------------------------------------
- config.py: resuelve y carga el env propio; descubre conexiones por alias.
- types.py: contratos (SSHConfig, RedshiftConfig, etc.).
- tunnel.py: manejo del túnel SSH.
- extractor.py: conexión a Redshift y API pública (list_databases, extract_sql), eventos y persistencia opcional.
- io.py: utilidades de escritura (si lo separas del extractor).
- cli.py: CLI/entrypoint (opcional).

--------------------------------------------------------------------------------
TROUBLESHOOTING
--------------------------------------------------------------------------------
- SSH auth falla: revisa usuario/key y permisos del .pem.
  - Linux/macOS: chmod 400 key.pem
- No llega a Redshift: verifica SG/VPC/rutas y REDSHIFT__<alias>__HOST/PORT.
- Password “truncado” o raro: usa comillas si hay #, espacios u otros caracteres.
- Alias no existe: revisa con list_databases() y confirma que el alias esté en el .env.redshift_extractor.

--------------------------------------------------------------------------------
SEGURIDAD
--------------------------------------------------------------------------------
- No commitees .env.redshift_extractor.
- Usa secretos del runtime cuando aplique.
- Mínimo privilegio (read-only si corresponde).
- La librería no imprime ni loggea credenciales.

--------------------------------------------------------------------------------
ROADMAP SUGERIDO
--------------------------------------------------------------------------------
- UNLOAD a S3 para grandes volúmenes.
- Streaming/chunks para evitar picos de RAM.
- Override de SSH por alias (si cambia bastion por entorno).
- Checks de calidad (nulls/duplicados) y métricas de operación.