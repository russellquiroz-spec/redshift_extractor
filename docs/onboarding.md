# Onboarding: de cero a `ping` en verde

Objetivo: que `redshift-extractor ping` conteste en verde. Si eso funciona, el tunel,
la host key, las credenciales y el cluster estan bien, y cualquier problema que quede
es del SQL.

Tiempo estimado: 15 minutos, mas lo que tarde en llegarte el acceso al bastion.

---

## 0. Lo que necesitas antes de empezar

| Cosa | Como se consigue |
|---|---|
| Python 3.10 o mas nuevo | `python --version` |
| Llave privada del bastion (`.pem`) | La entrega quien administra el bastion |
| Fingerprint SHA256 de la host key del bastion | Se lo pides a la misma persona. Ver el paso 4 |
| Usuario y password de Redshift | Los entrega el equipo de datos |
| Que tu IP publica este permitida en el Security Group | Lo hace quien administra AWS. Cambia al reconectar VPN o red |

La llave y las credenciales **nunca** van al repo.

---

## 1. Instalar

```
git clone <url-del-repo>
cd redshift_extractor
python install.py
```

`install.py` crea el venv, instala en modo editable y genera
`.env.redshift_extractor` desde `.env.example` si no existe.

Si vas a guardar Parquet, instala tambien el extra:

```
.venv\Scripts\pip install -e ".[parquet]"
```

Desde la version 0.3.0 `pyarrow` no viene incluido: son ~40 MB que solo hacen falta
para `save_parquet=True`.

---

## 2. Guardar las credenciales fuera del repo

El `.env` guarda **nombres** de variables, no valores. Crea una variable de sistema
con el usuario y el password juntos:

```
setx REDSHIFT_PROD_CREDENTIALS "{\"user\":\"tu_usuario\",\"password\":\"tu_password\"}"
```

Cierra y abre la terminal: `setx` no afecta a la que ya esta abierta.

Formatos aceptados para el valor de esa variable:

```
{"user":"db_user","password":"db_password"}
USER=db_user;PASSWORD=db_password
db_user:db_password
```

Si usas KeyringManager, el nombre tambien se resuelve desde
`%APPDATA%\KeyringManager\credentials.json`, que tiene prioridad.

---

## 3. Llenar `.env.redshift_extractor`

Lo minimo:

```
SSH_HOST=your.ssh.host
SSH_PORT=22
SSH_USER=ec2-user
SSH_PKEY_PATH=C:\ruta\absoluta\a\tu-llave.pem
SSH_LOCAL_PORT=0

DEFAULT_ALIAS=prod

REDSHIFT__prod__HOST=your-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__prod__PORT=5439
REDSHIFT__prod__DBNAME=analytics
REDSHIFT__prod__CREDENTIALS_ENV=REDSHIFT_PROD_CREDENTIALS
```

**Guardalo en UTF-8 sin BOM.** Si lo creas con PowerShell 5.1 (`Set-Content`, `>`,
`Out-File`) le mete BOM y la carga truena con un mensaje que dice como quitarlo. Un
editor con "UTF-8 sin BOM" evita el problema.

Comprueba que la config carga, sin tocar la red:

```
.venv\Scripts\redshift-extractor ls
```

Debe imprimir tus aliases. Si truena aqui, el problema es del archivo, no del acceso.

---

## 4. Registrar la host key del bastion

La libreria **no** acepta un servidor SSH desconocido: `AutoAddPolicy` esta prohibido.
Hay dos caminos.

**Camino recomendado, con fingerprint.** Pide el fingerprint a quien administra el
bastion, mira el que presenta el servidor y compara:

```
.venv\Scripts\redshift-extractor fingerprint --alias prod
```

Si coincide con el que te dieron, pegalo en el `.env`:

```
SSH_HOST_FINGERPRINT=SHA256:...
```

Si **no** coincide, no conectes: avisa a quien administra el bastion.

**Camino con known_hosts.** Solo si no puedes conseguir el fingerprint:

```
ssh-keyscan -p 22 your.ssh.host >> %USERPROFILE%\.ssh\known_hosts
```

Es mas debil, porque estas confiando en lo que conteste el host la primera vez.

---

## 5. `ping`

```
.venv\Scripts\redshift-extractor ping --alias prod
```

Verde se ve asi:

```
ok: True
alias: prod
server_version: PostgreSQL 8.0.2 on ..., Redshift ...
database: analytics
user: tu_usuario
redshift_host: your-cluster.xxxxxx.region.redshift.amazonaws.com
redshift_port: 5439
tunnel_port: 51234
latency_ms: 842.11
```

`database` y `user` los reporta el servidor, no el archivo de config: es la forma de
detectar que quedaste conectado a otro lado del que creias.

---

## 6. Primera extraccion

```python
from redshift_extractor import extract_sql

df = extract_sql("select current_date as hoy")   # alias = DEFAULT_ALIAS
print(df)
```

Con alias explicito y guardando a disco:

```python
df = extract_sql(
    "select * from esquema.tabla limit 100",
    alias="prod",
    save_dir="./output",
    save_csv=True,
)
```

Siempre devuelve el DataFrame; guardar es un efecto secundario opcional.

---

## Si `ping` no da verde

| Mensaje | Que significa | Que hacer |
|---|---|---|
| `No se encontro .env.redshift_extractor` | La libreria no encuentra su archivo | Copia `.env.example`, o define `REDSHIFT_EXTRACTOR_ENV_FILE` con ruta absoluta |
| `empieza con BOM` | Lo guardaste con PowerShell 5.1 | Reguardalo en UTF-8 sin BOM; el mensaje trae el comando exacto |
| `Timeout al conectar ... Security Group` | El puerto 22 no acepta tu IP publica actual, o el bastion esta apagado | Pide que agreguen tu IP. Cambia al reconectar VPN |
| `Conexion rechazada ... sshd` | El host responde pero no hay SSH ahi | Revisa `SSH_PORT` y que `sshd` este arriba |
| `no esta en ... known_hosts` | La host key no esta registrada | Paso 4 |
| `NO coincide` / `no coincide con ningun fingerprint` | La host key cambio | **No conectes.** Puede ser que recrearan el bastion, o que alguien intercepte. Verifica antes de actualizar |
| `Autenticacion SSH rechazada` | La llave no entra | Revisa `SSH_USER` y `SSH_PKEY_PATH`; en Linux/macOS `chmod 400` a la llave |
| `El puerto local N esta ocupado` | Fijaste `SSH_LOCAL_PORT` y algo mas lo tiene | Deja `SSH_LOCAL_PORT=0` |
| `del otro lado no contesta un servidor Redshift` | El tunel abrio pero apunta a un puerto sin cluster | Revisa `HOST` y `PORT` del alias |
| `Error psycopg2: ... password authentication failed` | El tunel esta bien; las credenciales no | Revisa la variable de sistema de `CREDENTIALS_ENV`; abre una terminal nueva si la acabas de crear |
| `El alias 'X' no existe` | Typo, o el alias no esta en el env | `redshift-extractor ls` |
| `No se indico alias y DEFAULT_ALIAS no esta definido` | Env sin `DEFAULT_ALIAS` | Definelo, o pasa `alias=` en cada llamada |

Codigos de salida del CLI: `0` bien, `1` negocio, `2` configuracion, `3` tunel.
