# Pendientes de homologacion - `redshift_extractor`

Estado de esta libreria contra `ESTANDAR.md` del ecosistema. Cada punto tiene estado,
razon y senal de cuando conviene hacerlo.

Ultima revision: 2026-08-27.

**Resumen:** ronda de homologacion cerrada. Se cerraron los once pendientes que tenia
esta libreria mas el retiro de las formas viejas, el test de divergencia y el shim de
credenciales. La suite paso de 13 a **166 tests**: 162 corren sin infraestructura, 3
son de integracion contra el bastion real y 1 depende de si `pyarrow` esta instalado.
CI en matriz 3.10 y 3.13.

Version publicada en esta ronda: **0.3.0**.

**Alcance de la ruptura:** 0.2.0 nunca se publico. Esta libreria pasa de 0.1.0 a 0.3.0
en un solo salto, asi que **los hosts no tienen ventana de deprecacion**: las formas
viejas no avisan, truenan. Decision tomada el 2026-08-27; el codigo retirado queda en
el historico de commits. La lista completa de lo que hay que editar esta en la seccion
"Cambios que rompen" y en la tabla de migracion del README.

---

## Cerrado en esta ronda

### 1. Tunel endurecido (I1, I4, I5, I6, H5, H6, H7) - OK

`tunnel.py` paso de 21 a ~590 lineas. Alcance exactamente el de DE-4:

| Criterio | Como quedo |
|---|---|
| I1 | Host key verificada **siempre**. `SSH_HOST_FINGERPRINT` (mas fuerte, verificado fuera de banda) o `known_hosts`. `AutoAddPolicy` no existe en el codigo |
| I4 | `atexit` mas handler de `SIGTERM` **encadenado** al previo. `SIGINT` no se toca. El handler nunca lanza (H9) |
| I5 | Health check de protocolo: SSLRequest del protocolo de PostgreSQL, que Redshift habla igual. Se corre al abrir, asi que un tunel contra el destino equivocado falla ahi y no despues como timeout de psycopg2 |
| I6 | `TunnelNetworkError`, `TunnelAuthError`, `TunnelHostKeyError`, `TunnelBindError`. El diagnostico que los distingue solo corre en el camino de error |
| H5 | `SSH_LOCAL_PORT=0` por default |
| H6 | Solo cierra lo que abrio (`owned=True`) |
| H7 | Snapshot y restore de las tres mutaciones globales de `sshtunnel.create_logger()`. El restore nunca lanza |
| Deadlock | `_abort_forwarder` cierra los sockets sin pasar por `stop()`. Antes, una llave vencida **colgaba el proceso** en vez de dar error |

**No se porto, por DE-4:** I3 (reuso por destino) ni I7 (`tunnel_status`,
`close_all_tunnels` publicos). Esta libreria abre un tunel por operacion. El registro
interno de tuneles existe solo para el cierre al salir, no para reusar. Hay un test que
falla si aparecen, para que agregarlos exija reabrir la decision.

`open_tunnel(ssh, redshift)` conserva su firma y sigue devolviendo el
`SSHTunnelForwarder`, para no romper a quien lea `tunnel.local_bind_port`.

El health check de apertura insiste hasta `SSH_CONNECT_TIMEOUT_S` (15 s por default) en
vez de un solo intento de 3 s. Un intento suelto abortaria un tunel valido pero lento
con el mensaje de "destino equivocado", que seria peor que el problema que el criterio
resuelve.

### 2. `errors.py` (F1, F2, F3) - OK

Raiz `RedshiftExtractorError` mas `ConfigError`, `EnvFileNotFoundError`, los cuatro
modos de falla del tunel y `QueryError`. Ninguna excepcion de paramiko, sshtunnel o
psycopg2 llega al usuario sin envolver.

**Divergencia deliberada de la referencia:** alli la raiz hereda de `Exception`. Aqui
hereda de `RuntimeError`, y `ConfigError` tambien de `ValueError`
(`EnvFileNotFoundError` tambien de `FileNotFoundError`). Razon: los hosts ya tienen
`except RuntimeError` y `except ValueError` alrededor de sus llamadas, porque es lo que
se lanzaba antes. Es lo unico que se conservo por compatibilidad, y a proposito: un
`except` que deja de atrapar falla en silencio, que es peor que un `TypeError`.

### 3. `events.py` (G1) - OK

`StatusEvent`, `OnEvent`, `emit()`, `KNOWN_EVENTS`, mas `register_secret()`/`redact()`
como red de seguridad. Un `on_event` que lanza no tumba la operacion.

**Divergencia deliberada:** los nombres de eventos quedan en MAYUSCULAS
(`TUNNEL_START`, `QUERY_OK`, ...) y no en minusculas como la referencia. Esta libreria
ya los emitia asi y los hosts filtran por esas cadenas exactas. El casing no lo
homologa el estandar. La maquinaria (`emit`, `redact`, `register_secret`,
`clear_secrets` y sus constantes) si es identica, y hay un test que lo comprueba.

El campo `db=` paso a `alias=` sin forma vieja, como manda el contrato del renombre, y
lo mismo con la clave `"alias"` de `ping()`.

### 4. Canon `alias` y retiro de las formas viejas (E2, E3, E4) - OK

Firma canonica, identica a la de la referencia:

```text
extract_sql(query=None, *, alias=None, query_file=None, ...)
ping(alias=None, *, on_event=None)
list_aliases()
```

`alias` es keyword-only en `extract_sql` y toma su default de `DEFAULT_ALIAS` del env
propio. Donde el alias ya era el primer posicional (`resolve`, `ping`) lo sigue siendo:
solo cambio de nombre.

**Retirado en el mismo cambio** (decision del 2026-08-27, sin ventana de deprecacion):

| Se fue | Reemplazo |
|---|---|
| El alias como primer posicional de `extract_sql` | `alias=` keyword |
| `db=` en `extract_sql`, `ping` y `config.resolve` | `alias=` |
| Los `*args` de `extract_sql`, que existian solo para acomodar los posicionales viejos | firma con nombres |
| `list_databases()`, `list_available_databases()` | `list_aliases()`, `list_available_aliases()` |
| `config.resolve_alias_arg()` y `DEPRECATED_DB_REMOVED_IN` | ya no hay nada que resolver |
| `--db` en el CLI | `--alias` |
| Los alias privados `config._find_env_file` / `config._read_own_env` | `find_env_file()` / `read_own_env()` |

`DEFAULT_DB` nunca existio en esta libreria, asi que no habia nada que deprecar en el
env.

`tests/test_alias_canon.py` (31 tests) fija las dos mitades: que `alias` esta en todas
las funciones publicas, y que ninguna forma vieja revivio. El parametrizado es el
patron copiado de la referencia, con la asercion invertida -`db` **no** debe estar-
porque aqui ya no hay periodo de gracia.

### 5. `ping()` (E6) - OK

Reporta `ok`, `alias`, `server_version`, `database`, `user`, `redshift_host`,
`redshift_port`, `tunnel_port` y `latency_ms`. `database` y `user` salen del servidor,
no de la config: es la forma de detectar un tunel apuntando al cluster equivocado. No
expone credenciales.

Expuesto tambien como `redshift-extractor ping`. Verificado contra el bastion y el
cluster real el 2026-08-27: `data-rabbit-prod` contesta en ~1.8 s.

**Hallazgo al probarlo:** el alias `dev` (cluster `redshift-mvp`) **no es alcanzable
desde el bastion**. El health check lo dice en 15 s con exit code 3 y un mensaje que
lista las tres causas probables; antes el sintoma era el `Timeout opening channel` que
ya estaba documentado en la referencia y que se confundio con un problema de la
libreria. Es infraestructura, no codigo: hay que revisar el Security Group del cluster
o si esta pausado.

### 6. Pines exactos en dependencias (B1, B3) - OK

`sshtunnel>=0.4`, `python-dotenv>=1.0`, `pandas>=2.0`, `psycopg2-binary>=2.9`,
`keyring>=24`, `typer>=0.12`, y rangos `>=` en el extra `dev`. El unico techo que queda
es `paramiko>=2.7.2,<4`, con su incompatibilidad concreta, la version que falla y la
fecha de verificacion.

El piso de pandas bajo de `2.2.3` a `2.0`: era esta libreria la que forzaba la version
en cualquier host que la instalara.

### 7. `pyarrow` como dependencia dura (A8) - OK

Movido a `[project.optional-dependencies] parquet = ["pyarrow>=12"]`. Pedirlo sin el
extra da un `ImportError` que dice el comando exacto de instalacion.

### 8. `LOG_LEVEL` sin prefijo (C3) - OK

Se lee del env propio. El override desde el entorno del proceso lleva prefijo:
`REDSHIFT_EXTRACTOR_LOG_LEVEL`. Un `LOG_LEVEL` suelto del host ya no se consume.

De paso se porto `logging.py` de la referencia: `get_logger()` con `NullHandler`,
`get_ssh_logger()` con `propagate=False`, y `configure_logging()` que configura **solo**
el logger propio. Con eso `basicConfig` desaparecio del paquete, lo que cierra G2, G3,
G4, H3 y H8. Era necesario: `emit()` necesita el logger propio y el tunel necesita
aislar a paramiko.

El CLI configura su consola en `WARNING`: los eventos INFO ya le llegan al usuario por
`on_event`, asi que mandarlos tambien al log los imprimia dos veces. Con `--debug`
bajan los dos.

### 9. Suite de tests (K1, K2) - OK

De 13 a 166 tests. Sin infraestructura corren 162; los 3 de integracion se saltan si el
bastion no responde y 1 se salta segun si `pyarrow` esta instalado. El CI corre
`-m "not integration"`.

`tests/sshserver.py` y `tests/fakepg.py` se copiaron **identicos** de la referencia: no
importan el paquete, asi que sirven tal cual. El docstring de `fakepg.py` menciona
`probe_postgres`, que aqui se llama `probe_redshift`; se dejo intacto a proposito para
conservar la igualdad.

Archivos nuevos: `conftest.py`, `test_tunnel.py` (24), `test_alias_canon.py` (31),
`test_convivencia.py` (H1-H8, C1-C3), `test_errores_y_eventos.py`, `test_config_env.py`
(BOM, fail-fast, fingerprints), `test_divergencia.py` (7), `test_io.py`,
`test_integration.py`.

`[tool.pytest.ini_options]` con `testpaths`, markers (`integration`, `sshserver`) y
`--basetemp=.pytest_tmp` (el default deja un symlink que en esta maquina no se puede
borrar y truena la limpieza de fin de sesion).

El test de fallo de autenticacion corre con timeout propio: si el rodeo del deadlock de
`sshtunnel` se pierde, el test se pone rojo en vez de colgar la suite entera.

### 10. Documentacion (K7, H10) - OK

- **`docs/onboarding.md`** (K7): de cero a `ping` en verde, con tabla de sintomas.
- **`docs/compatibilidad.md`** (H10): politica de dependencias, el techo de paramiko, el
  aislamiento del env, las garantias de convivencia y el diseno del test de divergencia.
- README: seccion de migracion a 0.3.0 con la tabla de que editar, `alias`, `ping`,
  errores tipados, extra `parquet`, codigos de salida del CLI.
- `queries/` sigue existiendo vacio en el working copy. Git no rastrea directorios
  vacios, asi que en el repo no hay nada que borrar; si alguien lo quiere usar, el README
  ya documenta como pasar rutas a `query_file`.

### 11. `py.typed` (A7) - OK

Archivo creado y declarado en `[tool.setuptools.package-data]`.

### 12. Test de divergencia de las copias duplicadas (D5) - OK

`tests/test_divergencia.py` compara contra los repos hermanos que esten en el directorio
de al lado y se salta con mensaje si no estan. El diseno y las tres decisiones que lo
hacen util -AST sin docstrings, constantes de modulo aparte, fin de linea normalizado-
estan en la seccion 8 de `docs/compatibilidad.md`.

Verificado con una prueba de mutacion: se rompieron a proposito `secret_loader.py`,
`_no_logging_side_effects` (H7), `_MIN_SECRET_LEN`, el codigo del `SSLRequest` y un doble
de test, y el test las cazo todas. Las dos ultimas **no** las cazaba la primera version,
que solo comparaba funciones: por eso las constantes de modulo se comparan aparte.

Hoy `secret_loader.py` es identico a las tres hermanas y la maquinaria de `events.py` es
identica a la de la referencia.

### 13. Retiro del shim `credentials.py` (D2) - OK

Borrado. Delegaba en `secret_loader`, que es donde vive la logica y sigue exportando
todos los nombres publicos (`parse_credentials_secret`, `resolve_secret_reference`,
etc.). Quien importaba desde `redshift_extractor.credentials` tiene que cambiar el
import; hay un test que comprueba que el modulo ya no existe.

### Extra: CI en matriz (K5) - OK

De una sola version a matriz `3.10` y `3.13` con `fail-fast: false`, instalando
`.[dev,parquet]` y excluyendo `integration`. Un `requires-python = ">=3.10"` que nadie
corria en su piso era un piso sin verificar.

El leg de 3.10 lo verifica el CI: en esta maquina solo hay 3.12 y 3.13. Los archivos del
repo se revisaron contra la sintaxis de 3.10 con `ast.parse(feature_version=(3,10))`.

### Extra: BOM fail-fast (DE-1) - OK

`config.py` lee con `encoding="utf-8"` y **falla** si el archivo empieza con BOM, con un
mensaje que incluye el comando de Python para reescribirlo. Antes usaba `utf-8-sig`, que
lo aceptaba en silencio.

### Extra: config que no se acepta en silencio (C7, F5) - OK

Una clave `REDSHIFT__*` que no calza con `REDSHIFT__<alias>__<CAMPO>` ahora es error en
vez de ignorarse. El caso tipico es el campo en minusculas (`REDSHIFT__prod__host`),
donde el usuario cree que configuro el host y no configuro nada. Los enteros y booleanos
invalidos truenan al cargar, no en la primera consulta.

---

## Cambios que rompen

Todo lo de esta tabla truena. No hay `DeprecationWarning` en ningun caso.

| Cambio | Que pasa si no se edita | Como encontrarlo |
|---|---|---|
| Alias posicional en `extract_sql` | `TypeError` | `findstr /s /n /c:"extract_sql(" *.py` |
| `db=` en `extract_sql`, `ping`, `resolve` | `TypeError` | el mismo grep |
| `list_databases()` / `list_available_databases()` | `AttributeError` o `ImportError` | `findstr /s /n /c:"list_databases" *.py` |
| `--db` en el CLI | `No such option: --db`, exit 2 | grep en `.ps1`, `.bat`, tareas programadas |
| `import redshift_extractor.credentials` | `ModuleNotFoundError` | `findstr /s /n /c:"credentials import" *.py` |
| `pyarrow` fuera de las dependencias duras | `ImportError` al pedir Parquet, con el comando de instalacion | `save_parquet=True` en el codigo |
| Host key verificada siempre | `TunnelHostKeyError` hasta registrar el bastion | paso 4 del onboarding |

**El unico caso que NO truena y hay que buscar a mano:** `extract_sql("prod")` con un
solo posicional. Antes ese posicional era el alias; ahora es el SQL, asi que la llamada
llega al cluster con `prod` como consulta y falla como error de SQL. La libreria no lo
puede detectar sin conectarse, y por eso el retiro va en un salto de version mayor.

Ademas, `extract_sql()` sin alias y sin `DEFAULT_ALIAS` en el env es un `ConfigError`
explicito. Antes el alias era obligatorio, asi que ningun host puede estar llamando sin
alias; el caso solo aparece al adoptar la forma nueva.

La verificacion de la host key es la unica ruptura que **no tiene forma vieja posible**:
aceptar cualquier host key *es* la vulnerabilidad que I1 cierra.

---

## Lo que queda

### A. Resolucion conjunta verificada en un host real (criterio 30 de la referencia)

**Estado: DOCUMENTADO, no se corre.** Decision del 2026-08-27: se deja escrito que si y
que no garantiza la politica de dependencias, en vez de guardar una evidencia que
caduca. La tabla de "que garantiza y que no" vive en `docs/compatibilidad.md` seccion 1,
que es el doc que lee un host.

**Que es.** La pregunta que responde no es "esta libreria funciona", sino "un proyecto
host puede instalar esta libreria **y otra del ecosistema** en el mismo venv sin que pip
se quede sin salida". Cada libreria declara sus dependencias por separado, pero el host
las instala juntas: pip tiene que encontrar **una sola** version de `pandas`, de
`paramiko`, de `python-dotenv`, etc. que satisfaga a todas a la vez. Si no existe, pip
falla con `ResolutionImpossible`, y el error aparece en el proyecto del usuario, no aca.

**Por que estaba en riesgo y por que ya casi no.** Hasta 0.1.0 esta libreria declaraba
`sshtunnel==0.4.0` y `python-dotenv==1.0.1`, pines exactos. Eso funcionaba solo porque
la referencia declaraba rangos laxos que los aceptaban: **la referencia se estaba
acomodando a esta libreria**. Con dos pines exactos incompatibles entre hermanas no hay
resolucion posible. Al pasar todo a rangos `>=` (pendiente 6), la causa principal
desaparecio. Lo que queda por hacer es comprobarlo, no arreglarlo.

**Que falta exactamente.** Correr la instalacion conjunta y guardar la evidencia:

```powershell
pip install --dry-run --ignore-installed --report reporte.json ^
  ./redshift_extractor ./postgresql_extractor_uploader
```

por cada par, y despues las seis juntas. La referencia ya tiene el ejercicio hecho para
sus pares y lo documento en su `docs/compatibilidad.md` seccion 1, con la tabla de
versiones que gana el resolvedor. Tambien tiene el test que lo automatiza
(`test_resolucion_conjunta_con_pip`), detras de una variable de entorno porque pega a la
red y tarda minutos.

**Lo que esa prueba no cubre.** Que pip resuelva no significa que las dos convivan en
ejecucion -que no se pisen el `.env`, el logging o el puerto del tunel-: eso es la
seccion H del estandar, y esa parte si esta cubierta por construccion y verificada en
`tests/test_convivencia.py` sin necesidad de las hermanas.

**Costo:** una tarde, casi todo esperando a pip. No hay codigo que escribir salvo el
test opcional.

**Por que se deja documentado y no corrido.** La evidencia caduca: cada release de
`pandas`, `paramiko` o `typer` cambia lo que elige el resolvedor, asi que un reporte de
hoy describe un estado que en un mes ya no es cierto. Ademas, la busqueda en
`Funciones/` no encontro **ningun** proyecto que importe dos de estas librerias a la
vez, asi que hoy la pregunta no tiene sujeto. Lo que si esta verificado -y es lo que
importa en el dia a dia- es la convivencia en ejecucion.

**Senal:** el primer proyecto host que instale dos o mas de estas librerias. Ahi la
corrida es de una tarde y la evidencia sirve porque hay un venv concreto que describir.

### B. `chunksize` / streaming / `UNLOAD` a S3

**Estado: DESCARTADO por ahora, con nota. Fuera del estandar.**

Decision del 2026-08-27: no se agrega parametro. Los casos puntuales se resuelven en el
llamador con un bucle por rango de fechas, que es mas simple y no le cuesta nada a los
demas. Queda anotado con su costo para cuando haya un caso real que no ceda con eso.

Hoy `extract_sql` trae todo el resultado a memoria y **no ha pasado nunca** que una
extraccion no quepa.

Hay tres cosas distintas debajo, con costos muy diferentes:

| Opcion | Que resuelve de verdad | Costo estimado |
|---|---|---|
| 1. `chunksize=N` que sigue devolviendo un DataFrame | **Nada de memoria.** Es lo que hace la referencia: `fetchmany` en bucle y `concat` al final. El pico es igual o peor, porque conviven los trozos y el resultado | ~20 lineas, 2-3 tests, medio dia |
| 2. Streaming real: cursor del servidor y escritura por trozo | Si acota el pico, pero cambia el contrato: no puede devolver el DataFrame completo (E7), asi que necesita otra funcion o un modo aparte | ~150 lineas mas docs, 1-2 dias, **y solo se puede probar contra el cluster real** |
| 3. `UNLOAD` a S3 | El unico camino razonable para volumenes muy grandes | Dias. Necesita bucket, IAM role en el cluster y `boto3` como dependencia nueva; cambia la superficie de seguridad |

El detalle que decide todo esto: **`pd.read_sql(chunksize=...)` sobre psycopg2 no acota
la memoria.** Un cursor normal de psycopg2 ya trajo el resultado completo al cliente
antes de que pandas vea el primer trozo. Para acotar de verdad hace falta un cursor con
nombre (`conn.cursor(name=...)`, que es `DECLARE`/`FETCH` del lado del servidor) mas
`itersize`, y eso arrastra manejo explicito de transaccion. Por eso la opcion 1 se ve
barata y no sirve, y la opcion 2 cuesta lo que cuesta.

El otro costo escondido de la opcion 2: los dobles en proceso (`tests/fakepg.py`)
contestan el handshake del protocolo pero no ejecutan SQL, asi que un camino de
streaming **no se puede cubrir sin infraestructura**. Seria la primera funcionalidad de
la libreria probada solo por tests de integracion, y eso contradice K2 para esa parte.

**Lo que se hace en su lugar:** el llamador parte la consulta por rango de fechas y
acumula. Cero cambios en la libreria, y el patron esta documentado en el README
(seccion "Extracciones grandes"):

```python
import pandas as pd
from redshift_extractor import extract_sql

SQL = "select * from ventas where fecha >= '{desde}' and fecha < '{hasta}'"

trozos = []
for inicio in pd.date_range("2026-01-01", "2026-06-01", freq="MS"):
    fin = inicio + pd.offsets.MonthBegin(1)
    trozos.append(
        extract_sql(SQL.format(desde=inicio.date(), hasta=fin.date()), alias="prod")
    )

df = pd.concat(trozos, ignore_index=True)
```

Tiene la ventaja de que cada trozo se puede guardar a disco y descartar, que es
justamente lo que la opcion 2 haria por dentro pero decidido por quien conoce el
volumen. El ejemplo completo, incluido el que guarda cada trozo sin acumularlo, esta en
la seccion "Extracciones grandes" del README.

Las fechas van interpoladas porque `extract_sql` no acepta parametros enlazados. Aqui es
inofensivo -las genera `pd.date_range`, no vienen de entrada de usuario- pero es la
limitacion que anota la seccion C.

**Senal para reabrirlo:** una extraccion que no quepa en RAM **ni partida por fechas**,
o una que tarde tanto que convenga `UNLOAD`. Cuando pase, empezar por la opcion 2 y
saltarse la 1, que no resuelve nada.

### C. `params` enlazados en `extract_sql`

**Estado: FALTA. Fuera del estandar, encontrado el 2026-08-27.**

`extract_sql` solo acepta el SQL como texto. La referencia acepta
`params: Optional[Dict[str, Any]]` y los enlaza con bindparams (`:nombre`), con el
comentario explicito de "nunca por interpolacion de texto".

Aqui, cualquier consulta con valores variables se arma con `format` o f-strings. Con
fechas generadas en el codigo es inofensivo, y es lo que hace el patron de la seccion B.
Deja de serlo el dia que un valor venga de entrada de usuario -un filtro de un dashboard,
un argumento de linea de comandos- porque entonces es inyeccion de SQL.

**Costo:** bajo. `psycopg2` ya enlaza con `%(nombre)s` y `cursor.execute(sql, params)`;
son ~10 lineas en `extract_sql` mas el paso de `params` a `pd.read_sql`. Lo que hay que
cuidar es no cambiar el comportamiento de quien ya tiene `%` literales en su SQL: con
`params=None` no debe tocarse nada.

**Senal:** la primera consulta cuyo filtro venga de fuera del codigo. Hoy todas las
llamadas conocidas son SQL fijo o fechas generadas.

---

## Estado por seccion del estandar

| Seccion | Estado |
|---|---|
| A. Empaquetado | OK |
| B. Dependencias | OK. La resolucion conjunta queda documentada, no corrida (A de arriba) |
| C. Configuracion y aislamiento | OK |
| D. Credenciales | OK. `secret_loader` unico (shim retirado) y divergencia cubierta por test |
| E. API publica | OK |
| F. Errores | OK. `negocio=1` en el CLI en vez de `4`, igual que la referencia y que el comportamiento historico de esta libreria |
| G. Eventos y logging | OK |
| H. Convivencia | OK |
| I. Tunel | OK en el alcance de DE-4. I3 e I7 descartados con razon, con un test que impide que reaparezcan por accidente |
| J. Escritura | n/a, esta libreria no escribe |
| K. Calidad y documentacion | OK |

---

## F4: el codigo de salida 2 significa dos cosas distintas

**Agregado el 2026-08-27 desde `mongo_extractor`, que lo encontro y lo arreglo de su
lado. Aplica igual aqui.**

### Hallazgo 1: `typer`/`click` ya usa el 2, y choca con `EXIT_CONFIG`

Verificado empiricamente: click sale con **2** ante un flag invalido, un argumento
obligatorio faltante o un subcomando inexistente. Ese es el mismo codigo que el
ecosistema asigno a los errores de configuracion, asi que:

```
<cli> ls --alais tx        -> exit 2   (typo en el flag)
<cli> ls                   -> exit 2   (.env roto)
```

Un script de CI o un runner que trate el 2 como "problema de configuracion" se equivoca
ante cualquier typo en la linea de comandos. F4 promete codigos **estables**, y un codigo
que significa dos cosas no lo es.

### Hallazgo 2: el texto del criterio dice `negocio=4`; nadie lo implementa asi

Censo del 2026-08-27:

| Libreria | negocio | config | tunel |
|---|---|---|---|
| `postgres_local_client` | 1 | 2 | 3 |
| `redshift_extractor` | 1 | 2 | 3 |
| `netsuite_extractor` | 1 | 2 | n/a |
| `mongo_extractor` | 1 (era 4) | 2 | 3 |

`mongo_extractor` se alineo a **1**, que es lo que hacen las otras tres y lo que dice la
convencion de Unix. La razon de fondo: con `negocio=4`, el codigo **1 queda
inalcanzable**, porque el `_guarded` de todos los CLI atrapa `Exception`; no existe
ningun camino que produzca un 1, y un codigo que nunca ocurre no distingue nada.

**Lo que hay que corregir es el texto del ESTANDAR (F4), no las cuatro librerias.**

### El arreglo, ya implementado en `mongo_extractor`

Los errores de USO salen con **64** (`EX_USAGE` de `sysexits.h`), separados de los de
config. Se hace en el entry point, sin tocar estado global de click:

```python
def main() -> None:
    try:
        codigo = app(standalone_mode=False)
    except click.UsageError as exc:
        exc.show()
        raise SystemExit(EXIT_USAGE)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code)
    except click.exceptions.Abort:
        raise SystemExit(EXIT_INTERRUPTED)   # 130

    raise SystemExit(codigo if isinstance(codigo, int) else EXIT_OK)
```

Y el entry point de `[project.scripts]` pasa de `<paquete>.cli:app` a
`<paquete>.cli:main`.

**La trampa que cuesta media hora si no se sabe:** con `standalone_mode=False`, click
**devuelve** el codigo de un `typer.Exit` como valor de retorno y solo **levanta** las
excepciones de usuario. Si se tratan igual -atrapando `click.exceptions.Exit` y
esperando que se levante- **todos los errores salen con 0**. Hay que usar el valor de
retorno.

Cuadro final:

| Codigo | Significado |
|---|---|
| 0 | ok |
| 1 | negocio |
| 2 | configuracion |
| 3 | tunel |
| 64 | error de uso de la linea de comandos |
| 130 | interrumpido con Ctrl+C |

**Que copiar:** `mongo_extractor/src/mongo_extractor/cli.py` (constantes y `main()`) y
`mongo_extractor/tests/test_cli_exit_codes.py`, que prueba los diez casos corriendo el
entry point real -con `CliRunner` sobre `app` no se ejercitaria, porque la separacion
vive en `main()`.

**Senal:** ninguna urgente si nadie automatiza sobre los codigos de salida. Es una
edicion de ~15 lineas mas una linea en `pyproject.toml`.
