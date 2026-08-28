# Pendientes de homologacion - `redshift_extractor`

Estado de esta libreria contra `ESTANDAR.md` del ecosistema. Cada punto tiene estado,
razon y senal de cuando conviene hacerlo.

Ultima revision: 2026-08-27.

**Resumen:** ronda de homologacion cerrada, y cerrado tambien todo lo que quedaba
pendiente de hacer. Primero se cerraron los once que tenia esta libreria, mas el retiro
de las formas viejas, el test de divergencia y el shim de credenciales; despues los
cuatro hallazgos de la validacion funcional (E, F, G, H), F4 -que llego desde
`mongo_extractor`- y por ultimo C y D. La suite paso de 13 a **247 tests**: 243 corren
sin infraestructura, 3 son de integracion contra el bastion real y 1 depende de si
`pyarrow` esta instalado.

**CI verde en 3.10 y 3.13 el 2026-08-27, por primera vez en la historia del repo.** Ver
la seccion del CI mas abajo: estaba en rojo desde su primera corrida y este documento
afirmaba lo contrario.

Version publicada en la ronda de homologacion: **0.3.0**. Lo cerrado despues esta sin
publicar; ver `CHANGELOG.md`.

**Validacion funcional del 2026-08-27, despues de cerrar la ronda.** Se corrio la
herramienta completa -CLI y API- contra el cluster de produccion con un query real de
negocio (bono por ruta, 416 filas, 29 columnas). Todo lo homologado respondio como dice
el README: codigos de salida, errores tipados, `ping`, aliases, persistencia CSV y
Parquet, eventos, las cuatro formas de `query_file` y las firmas viejas truenando.
Salieron cuatro cosas nuevas -un bug del modo prueba de `run-file` (E), eventos
duplicados en el stream (F), `cli.py` sin pruebas (G) y un warning de pandas que se
filtra a consola (H)-, **todas cerradas ya**; estan en "Cerrado despues de la validacion
funcional", con la evidencia de haber reproducido cada una antes de tocarla.

**Lo que sigue abierto no es trabajo, son dos decisiones ya tomadas:** A -documentar que
garantiza la politica de dependencias en vez de guardar una evidencia que caduca- y B
-no agregar `chunksize`/streaming/`UNLOAD`, resolverlo en el llamador partiendo por
fechas-. Las dos tienen escrita su senal de cuando conviene reabrirlas. C y D, que si
eran trabajo, quedaron cerradas.

**Alcance de la ruptura:** 0.2.0 nunca se publico. Esta libreria pasa de 0.1.0 a 0.3.0
en un solo salto, asi que **los hosts no tienen ventana de deprecacion**: las formas
viejas no avisan, truenan. Decision tomada el 2026-08-27; el codigo retirado queda en
el historico de commits. La lista completa de lo que hay que editar esta en la seccion
"Cambios que rompen" y en la tabla de migracion del README.

---

## Cerrado despues de la validacion funcional (post-0.3.0)

Los cuatro hallazgos de la corrida contra produccion del 2026-08-27 (E, F, G, H), mas F4
-que llego desde `mongo_extractor`- y los dos que quedaban de la lista original: C y D.

**Cada uno se reprodujo antes de tocarlo**, en el venv del proyecto y no en el Python
global; ninguno estaba ya arreglado, y la evidencia de la reproduccion esta en su
seccion. Vale la advertencia: correr la suite con el interprete equivocado da tres
fallos de `run-file` que no tienen nada que ver con esta libreria -son typer 0.12.5
contra click 8.3.1, incompatibles entre si- y hacen perder el rato. El venv del proyecto
trae typer 0.25.1 con click 8.3.3.

### E. `run-file` en modo prueba rechazaba cualquier `.sql` que empezara con comentario - OK

**Reproducido** el 2026-08-27 con `queries/bono_ruta_v3.sql`, el query real de negocio:
`apply_limit(sql, 10)` -> `ValueError: El modo LIMIT solo funciona con SELECT/WITH`.

`apply_limit` decidia si podia envolver el SQL mirando la primera palabra del archivo
sin quitar comentarios, asi que cualquier query documentada entregaba `--` como primera
palabra. Como el modo prueba es el **default** de `run-file`, el workaround era `--full`,
justo lo que ese modo existe para evitar.

**Como quedo.** Nueva `cli.first_keyword()`: consume espacios, comentarios `--` y
comentarios `/* */` desde el inicio y se para en el primer token real. El SQL que se
ejecuta es el original, con sus comentarios intactos; solo la decision los ignora.

Sobre el `--` dentro de una cadena literal, que era el riesgo anotado: no se presenta.
Al consumir solo el prefijo y parar en el primer token, una cadena literal nunca se
alcanza antes de decidir. Queda un test que lo fija de todas formas.

Dos correcciones que venian con el mismo bug:

- El mensaje de rechazo nombra la palabra que si encontro (`empieza con 'insert'`) en
  vez de culpar al tipo de sentencia, que era justo lo unico que no estaba mal.
- Un archivo de puros comentarios ya no cae en el mensaje de SELECT/WITH: da
  "no trae ninguna sentencia: solo comentarios".

**Cubierto por** `tests/test_cli.py`: 28 tests, entre ellos el header de comentarios, el
bloque `/* */`, el bloque sin cerrar, el `--` dentro de cadena, el `;` final, los
comentarios interiores y que el rechazo de `insert`/`update` siga vivo -que antes
pasaba por accidente, porque cualquier header lo disparaba-.

### F. `QUERY_START` y `TUNNEL_START` se emitian dos veces por extraccion - OK

**Reproducido** por lectura y confirmado con la secuencia capturada. Una extraccion con
persistencia emitia:

```text
CONFIG_LOADED, ALIAS_RESOLVED, TUNNEL_START, QUERY_START, TUNNEL_START,
TUNNEL_READY, DB_CONNECT_START, DB_CONNECTED, QUERY_START, QUERY_OK,
FILE_SAVED, CONNECTION_CLOSED, TUNNEL_CLOSED, DONE
```

Y ahora:

```text
CONFIG_LOADED, ALIAS_RESOLVED, SAVE_CONFIGURED, TUNNEL_START, TUNNEL_READY,
DB_CONNECT_START, DB_CONNECTED, QUERY_START, QUERY_OK, FILE_SAVED,
CONNECTION_CLOSED, TUNNEL_CLOSED, DONE
```

**Como quedo.** Las dos causas, cada una por su lado:

- El aviso de "guardado activado" pasa de `QUERY_START` a **`SAVE_CONFIGURED`**, que se
  agrego a `KNOWN_EVENTS` y al `Literal` de `extractor.EventType`. Ademas ahora lleva
  `alias`, que le faltaba.
- Se borro el `TUNNEL_START` de `extract_sql`. Queda el de `tunnel.open_tunnel`, que es
  el unico que conoce el puerto local; `alias` y `redshift_dbname` se mudaron alla para
  no perderlos. `open_tunnel` acepta un `alias=` keyword-only opcional para recibirlo,
  asi que `open_tunnel(ssh, rs)` sigue construyendo igual (E8). `ping()` tambien lo pasa.

**Nota sobre el `FILE_SAVED` doble** que aparecia en la captura original: no era bug. Sale
una vez por archivo escrito, y esa corrida guardo CSV y Parquet.

**Cubierto por** cinco tests nuevos en `tests/test_errores_y_eventos.py`, que corren la
extraccion completa contra el tunel de prueba -handshake SSH real- con la consulta
sustituida, porque `tests/fakepg.py` contesta el protocolo pero no ejecuta SQL. Fijan que
`TUNNEL_START`, `TUNNEL_READY`, `QUERY_START`, `QUERY_OK` y `SAVE_CONFIGURED` salen
exactamente una vez, que el `TUNNEL_START` que queda trae puerto y alias, y que ningun
evento emitido queda fuera del catalogo.

**Rompe a quien consuma eventos**, y a nadie mas: un filtro por `QUERY_START` deja de ver
el aviso de guardado, y un contador que compensara la duplicacion de `TUNNEL_START` ahora
cuenta de menos. El CLI solo los imprime.

### H. El `UserWarning` de pandas se filtraba a consola en cada corrida - OK

**Reproducido** el 2026-08-27: `pd.read_sql` sobre cualquier conexion DBAPI2 emite el
`UserWarning` de "pandas only supports SQLAlchemy connectable". Salia en cada extraccion,
por CLI y por API, y tambien al final de cada corrida de la suite.

**Como quedo.** `extractor._read_sql_sin_el_warning_de_sqlalchemy()` envuelve la llamada
con `warnings.catch_warnings()` y silencia **ese** mensaje y **esa** categoria. Nada de
filtros globales: la libreria no toca la configuracion de warnings del host, por el mismo
principio de C3 con el logging.

**Efecto colateral util:** la suite dejo de terminar con un `warnings summary`.

**Se cruzo con un test existente.** `test_convivencia.py::test_g5_no_deja_filterwarnings_permanente`
prohibia la cadena `filterwarnings` en cualquier modulo, por substring. El nombre dice
"permanente", que es la propiedad que de verdad importa, pero el substring no distingue
un filtro global de uno acotado que se revierte al salir. Se reescribio por **AST**: ahora
busca `filterwarnings`, `simplefilter` y `resetwarnings` que **no** esten dentro de un
`with warnings.catch_warnings():`. Verificado que sigue atrapando el uso incorrecto -filtro
global suelto, `simplefilter` suelto y `resetwarnings`- y que permite el correcto, incluido
el anidado.

**Cubierto por** dos tests en `tests/test_errores_y_eventos.py`: que el warning no se filtra,
y que los filtros globales quedan exactamente como estaban despues de la llamada.

### G. `cli.py` no tenia pruebas - OK

**Reproducido** el 2026-08-27: la unica mencion de `run_file` en toda la suite estaba en
`test_alias_canon.py`, que comprueba por introspeccion que la opcion se llama `--alias` y
no ejecuta el comando. Las seis funciones -`apply_limit`, `read_sql`,
`strip_trailing_semicolons`, `is_connection_error`, `execute_with_retries` y
`print_result`- tenian cero cobertura, y por eso E vivio hasta encontrarse a mano.

**Como quedo.** `tests/test_cli.py`, **65 tests**, todos sin infraestructura. La tabla del
pendiente original, cubierta entera:

| Caso | Donde |
|---|---|
| `apply_limit` con header de comentarios, `;` final y `--` interiores | `test_first_keyword_*`, `test_apply_limit_*` |
| `apply_limit` con `insert`/`update` | `test_apply_limit_sigue_rechazando_lo_que_no_es_select` |
| `apply_limit` con `--limit 0` y negativo | `test_apply_limit_rechaza_limite_no_positivo` |
| `read_sql` con archivo inexistente, con directorio y con UTF-8 acentuado | `test_read_sql_*` |
| Codigos de salida del entry point real | `test_error_de_uso_sale_con_64` y companía, que es tambien el cierre de F4 |

Se cubrieron ademas dos que no estaban en la lista y valen igual:

- `is_connection_error`, que decide si `run-file` reintenta. Un falso positivo reintenta
  tres veces un error de SQL que nunca va a cambiar; un falso negativo desperdicia los
  reintentos. Se fija con siete errores de red y tres de SQL.
- `execute_with_retries`, incluido que **no** reintente un error de sintaxis y que agote
  los intentos antes de relanzar.

### F4. El codigo de salida 2 significaba dos cosas distintas - OK

**Reproducido** el 2026-08-27 contra el entry point instalado: `redshift-extractor ls
--alais prod` (typo en el flag) salia con **2**, el mismo codigo que un `.env` roto. Igual
`subcomando-inexistente` y `run-file` sin su argumento.

**Como quedo.** Copiado de `mongo_extractor`, que lo encontro y lo arreglo primero:

| Codigo | Significado |
|---|---|
| 0 | ok |
| 1 | negocio |
| 2 | configuracion |
| 3 | tunel |
| 64 | error de uso de la linea de comandos (`EX_USAGE` de `sysexits.h`) |
| 130 | interrumpido con Ctrl+C |

`cli.main()` es el entry point nuevo y `[project.scripts]` apunta a `cli:main` en vez de
`cli:app`. La separacion vive ahi, sin tocar estado global de click.

**Sobre el hallazgo 2 del pendiente** -que el texto del ESTANDAR dice `negocio=4` y nadie
lo implementa asi-: esta libreria ya usaba `negocio=1`, que es lo que hacen las cuatro. No
habia nada que cambiar aqui. **Lo que sigue pendiente es corregir el texto de F4 en
`ESTANDAR.md`**, que es del ecosistema y no de este repo.

**La trampa que advertia el pendiente, verificada.** Con `standalone_mode=False` click
**devuelve** el codigo de un `typer.Exit` como valor de retorno y solo **levanta** las
excepciones de uso. Tratarlas igual hace que todo salga con 0. `main()` usa el valor de
retorno, y hay un test que fija que un `.env` roto sigue saliendo con 2 justamente para
atrapar esa regresion.

**Un detalle mas que el pendiente no traia**, y que `mongo_extractor` si resolvio: typer
0.27 dejo de depender del paquete `click` y trae una copia vendorizada en `typer._click`,
cuyas excepciones son clases distintas. Un `except click.UsageError` no matchea ahi y el
error escaparia como traceback. `pyproject.toml` declara `typer>=0.12`, asi que las dos
formas estan permitidas: `modulo_de_excepciones()` resuelve la correcta en tiempo de
ejecucion desde la clase base del comando, y si no lo logra `main()` se cae al modo
estandar de typer en vez de tronar. Este venv corre typer 0.25.1, que usa `click`.

**Cubierto por** los tests de codigos de salida en `tests/test_cli.py`, que corren
`main()` y no `app` con `CliRunner`: la separacion vive en `main()`, asi que con
`CliRunner` sobre `app` estos tests pasarian sin probar nada. Hay ademas uno que lee
`pyproject.toml` y falla si el entry point vuelve a apuntar a `:app`, porque el resto
seguiria en verde.

### C. `params` enlazados en `extract_sql` - OK

**Reproducido** el 2026-08-27 por introspeccion: `params` no estaba en la firma. Se
cerro sin esperar la senal -"la primera consulta cuyo filtro venga de fuera del
codigo"-, porque el costo era bajo y la senal es justo el momento en que ya es tarde.

**Como quedo.** `params: Optional[Dict[str, Any]] = None`, keyword-only. Los enlaza
psycopg2 con su marcador nativo `%(nombre)s`:

```python
extract_sql(
    "select * from ventas where ruta_id = %(ruta)s and fecha >= %(desde)s",
    params={"ruta": ruta_id, "desde": "2026-01-01"},
    alias="prod",
)
```

Funciona igual con `query_file`, asi que un `.sql` versionado puede llevar sus
marcadores y recibir los valores desde el codigo.

**Divergencia deliberada de la referencia**, que enlaza con `:nombre` porque usa
SQLAlchemy. Aqui es `%(nombre)s` y **no se traduce** de uno al otro: en Redshift `::` es
el operador de cast y sale en casi cualquier query real, asi que un traductor de
`:nombre` tendria que distinguirlo del cast y de los `:` dentro de cadenas literales.
Fragil, y sin nada que ganar sobre el marcador que psycopg2 ya entiende.

**La parte delicada, que el pendiente anotaba:** que `params=None` no cambie nada. Se
resolvio no pasandole el argumento a `pd.read_sql` en ese caso, en vez de pasarle `None`.
No es cosmetico: psycopg2 solo interpreta `%` cuando recibe parametros, asi que un
`params=None` explicito le cambiaria el significado a un SQL con `%` literales -un
`like '%rabbit%'`, un `to_char(x, '%Y')`- que hoy funciona.

**Cubierto por** `tests/test_params.py`, 9 tests: que el marcador llega intacto y el
valor viaja aparte, que un valor malicioso (`1; drop table ventas--`) no se convierte en
SQL, que funciona con `query_file`, que sin `params` el argumento ni se pasa, y tres
casos de `%` literales que tienen que seguir intactos.

### D. La guarda de plataforma de `secret_loader.py` - OK

**Reproducido** el 2026-08-27: `mypy src --platform linux` daba los cuatro errores
`attr-defined` sobre `winreg.HKEY_CURRENT_USER`, `HKEY_LOCAL_MACHINE`, `OpenKey` y
`QueryValueEx`.

**Como quedo.** La guarda es `sys.platform != "win32"` en vez de `os.name != "nt"`, que
mypy si entiende como estrechamiento de plataforma y que en runtime hace exactamente lo
mismo. El cambio esta en las cuatro copias del ecosistema
-`postgresql_extractor_uploader`, `mongo_extractor`, `netsuite_extractor` y esta-, que es
como tenia que ir para no desalinearlas: `tests/test_divergencia.py` compara el archivo
completo y pasa.

**Y se retiro el workaround.** `[tool.mypy]` tenia `platform = "win32"` declarado solo
para tapar esos cuatro errores. Ya no hace falta, y quitarlo tiene valor propio: con el
workaround puesto, el typecheck **daba por bueno** todo el codigo especifico de Windows
en vez de revisarlo. Verificado en las dos plataformas y sin declarar ninguna:

```text
mypy src --platform linux  -> Success: no issues found in 11 source files
mypy src --platform win32  -> Success: no issues found in 11 source files
mypy src                   -> Success: no issues found in 11 source files
```

**La sospecha del pendiente era correcta:** las hermanas tenian el mismo `os.name`, y
ninguna declara `platform` en su `[tool.mypy]`, asi que sus CI estaban fallando por lo
mismo. Con el cambio en las cuatro, eso queda cerrado tambien alla.

**`redshift_uploader` no aplica:** no tiene `secret_loader.py` y tampoco esta en la lista
de hermanas de `test_divergencia.py`.

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

**Hallazgo al probarlo:** el alias `dev` **no es alcanzable
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

Dos aserciones se reescribieron al primer contacto con el runner, porque probaban el
sistema operativo y no la libreria:

- El puerto efimero se verificaba abriendo dos tuneles **en secuencia** y exigiendo
  puertos distintos. El sistema operativo puede reasignar el mismo puerto al segundo: el
  primero ya lo solto y es libre de hacerlo. Ahora se abren a la vez, que es cuando la
  libreria si tiene que dar puertos distintos.
- La fuga de hilos asumia que un segundo alcanzaba para que paramiko cerrara los suyos.
  Ahora espera hasta 10 s a que bajen. El limite sigue en 5: lo que se tolera es la
  lentitud de una maquina cargada, no la fuga.

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

### Extra: CI en matriz y en todas las ramas (K5) - OK

De una sola version a matriz `3.10` y `3.13` con `fail-fast: false`, instalando
`.[dev,parquet]` y excluyendo `integration`. Un `requires-python = ">=3.10"` que nadie
corria en su piso era un piso sin verificar.

El trigger tambien cambio: escuchaba `push` solo en `main`, asi que una rama de trabajo
no disparaba nada y el fallo se descubria al abrir el PR. Ahora corre en todas las
ramas, con `concurrency` para que cada push cancele la corrida anterior de su mismo ref.

**Correccion de estado: el CI de este repo nunca estuvo verde.** La revision anterior de
este documento decia "CI verde con ruff + mypy + pytest" y la tabla del ESTANDAR marcaba
K5 como OK; las dos cosas eran falsas. Las 13 corridas del historial fallaron, todas en
`main` y todas en mypy, desde la primera. Quedo verde en la corrida 17, el 2026-08-27.

Los tres fallos que habia, en cadena. Ninguno se reproducia en Windows, que es por lo
que sobrevivieron tanto:

| # | Fallo | Causa | Arreglo |
|---|---|---|---|
| 1 | mypy, cuatro errores `attr-defined` | `secret_loader` importa `winreg` bajo la guarda `os.name != "nt"` y mypy estrecha por `sys.platform`. **Preexistente:** mypy 1.11.2, la version que el CI pineaba antes, falla igual. No fue el cambio de pines de esta ronda | `platform = "win32"` en `[tool.mypy]`. El de fondo, en la seccion D de "Lo que queda" |
| 2 | Cuatro tests del CLI | Afirmaban `"--alias" in resultado.output` sobre el texto de `--help`. Typer lo dibuja con rich: el ancho del runner y los codigos de color parten los nombres de las opciones | Leer la declaracion del comando, no el render |
| 3 | Los mismos cuatro | El primer arreglo introspeccionaba el objeto de click, y el typer que resuelve el CI no lo expone como modulo propio. Importar una transitiva en un test contradice B5 | Leer la firma de la funcion y su `OptionInfo`, que es lo que este repo declara |

Para diagnosticarlos hubo que agregar un paso que publica el fallo como **anotacion del
check**: el log del job pide permisos de lectura de Actions, y sin eso el unico dato
disponible era "Process completed with exit code 1". Se queda, porque es lo que convirtio
ese mensaje en un diagnostico.

**La leccion, que aplica a las cuatro librerias:** un test verde en Windows no dice nada
del runner. Los dos fallos de tests no eran de la libreria sino de aserciones que
probaban el entorno -el renderizado de rich, la resolucion de dependencias- en vez del
contrato.

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

### Lo que rompe de la ronda posterior a 0.3.0

Nada de esto afecta a quien solo llame `extract_sql` o use el CLI a mano. Toca a dos
tipos de consumidor:

| Cambio | A quien le pega | Como encontrarlo |
|---|---|---|
| El aviso de guardado pasa de `QUERY_START` a `SAVE_CONFIGURED` | Quien filtre o cuente eventos | `findstr /s /n /c:"QUERY_START" *.py` |
| `TUNNEL_START` se emite una vez, no dos | Quien cuente eventos o mida latencia del tunel | `findstr /s /n /c:"TUNNEL_START" *.py` |
| Los errores de uso del CLI salen con **64** y ya no con 2 | Scripts, tareas programadas y CI que revisen el codigo de salida | grep de `errorlevel` en `.bat`, `$LASTEXITCODE` en `.ps1` |

El cambio de codigos de salida es el unico con riesgo real de pasar inadvertido: un
script que trataba el 2 como "error de configuracion" ahora ve un 64 ante un typo en el
comando y no lo reconoce. A cambio, el 2 por fin significa una sola cosa.

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

Las fechas van interpoladas porque las genera `pd.date_range` en el propio codigo, no
una entrada de usuario, asi que ahi es inofensivo. Si el rango viniera de fuera, va con
`params` (cerrado; ver la seccion C en los cerrados).

**Senal para reabrirlo:** una extraccion que no quepa en RAM **ni partida por fechas**,
o una que tarde tanto que convenga `UNLOAD`. Cuando pase, empezar por la opcion 2 y
saltarse la 1, que no resuelve nada.

---

## Estado por seccion del estandar

| Seccion | Estado |
|---|---|
| A. Empaquetado | OK |
| B. Dependencias | OK. La resolucion conjunta queda documentada, no corrida (A de arriba) |
| C. Configuracion y aislamiento | OK |
| D. Credenciales | OK. `secret_loader` unico (shim retirado), divergencia cubierta por test y la guarda de plataforma alineada en las cuatro librerias (D de arriba) |
| E. API publica | OK. `extract_sql` acepta `params` enlazados, que era lo unico que le faltaba contra la referencia (C de arriba) |
| F. Errores | OK. `negocio=1` en el CLI en vez de `4`, igual que la referencia y que el comportamiento historico de esta libreria. Los errores de **uso** se separaron del 2 y salen con 64 (F4 de arriba); lo que queda es corregir el texto del ESTANDAR, no el codigo |
| G. Eventos y logging | OK en catalogo, campos, aislamiento del logging y **secuencia**: los dos eventos duplicados se cerraron (F de arriba) y hay tests que los fijan |
| H. Convivencia | OK |
| I. Tunel | OK en el alcance de DE-4. I3 e I7 descartados con razon, con un test que impide que reaparezcan por accidente |
| J. Escritura | n/a, esta libreria no escribe |
| K. Calidad y documentacion | OK. El CI quedo verde en 3.10 y 3.13 el 2026-08-27, por primera vez en el repo: estaba rojo desde su primera corrida y este documento lo daba por bueno. La suite ya cubre tambien el CLI, que era el hueco de K1/K2 (G de arriba), y mypy volvio a typechequear el codigo de Windows al retirarse el `platform = "win32"` (D de arriba) |

---

## F4 en el ESTANDAR: el texto sigue diciendo `negocio=4`

**Lo de este repo ya esta cerrado** (ver "F4" en los cerrados de arriba): el 64 se separo
del 2 y esta libreria ya usaba `negocio=1`. Lo que queda es una correccion **al ESTANDAR**,
no a las librerias.

El texto del criterio F4 dice `negocio=4`. Censo del 2026-08-27:

| Libreria | negocio | config | tunel |
|---|---|---|---|
| `postgres_local_client` | 1 | 2 | 3 |
| `redshift_extractor` | 1 | 2 | 3 |
| `netsuite_extractor` | 1 | n/a | n/a |
| `mongo_extractor` | 1 (era 4) | 2 | 3 |

Ninguna implementa el 4, y `mongo_extractor` se alineo a 1 despues de haberlo intentado.
La razon de fondo: con `negocio=4`, el codigo **1 queda inalcanzable**, porque el guard de
todos los CLI atrapa `Exception`; no existe ningun camino que produzca un 1, y un codigo
que nunca ocurre no distingue nada. El 1 es ademas la convencion de Unix.

**Lo que hay que corregir es el texto del ESTANDAR, no las cuatro librerias.**

**Senal:** la proxima pasada que toque `ESTANDAR.md`. No hay codigo que escribir.
