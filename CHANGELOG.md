# Changelog

Este archivo empieza en 0.3.0. Para lo anterior, el historico de commits: hasta 0.1.0 el
proyecto no llevaba changelog.

## 0.4.0 - 2026-08-27

Cierra los cuatro hallazgos de la validacion funcional del 2026-08-27 (E, F, G, H de
`docs/pendientes.md`), F4 -que llego desde `mongo_extractor`- y los dos que quedaban de
la lista original: C (`params` enlazados) y D (la guarda de plataforma de
`secret_loader`). Con esto `docs/pendientes.md` no tiene trabajo abierto: lo que queda
son dos decisiones ya tomadas, con su senal escrita.

Menor y no parche porque hay cambios que rompen, aunque solo a quien consuma eventos o
codigos de salida; la tabla esta abajo. Para quien llame `extract_sql` o use el CLI a
mano no cambia nada.

### Arregla

- **El modo prueba de `run-file` ya acepta archivos con encabezado de comentarios.**
  `apply_limit` decidia si podia envolver el SQL con `LIMIT` mirando la primera palabra
  del archivo sin quitar comentarios, asi que cualquier `.sql` que empezara con
  `-- ...` —o sea, cualquier query documentada— entregaba `--` como primera palabra y se
  rechazaba con "El modo LIMIT solo funciona con SELECT/WITH". Como el modo prueba es el
  **default** del comando, el workaround era `--full`, justo lo que ese modo existe para
  evitar. Ahora la nueva `cli.first_keyword()` salta comentarios de linea y de bloque
  antes de tomar el primer token; el SQL que se ejecuta sigue siendo el original, con sus
  comentarios. El mensaje de rechazo ademas nombra la palabra que si encontro, en vez de
  culpar al tipo de sentencia.

- **`TUNNEL_START` y `QUERY_START` ya no se emiten dos veces por extraccion.** El
  catalogo y los campos estaban bien; lo que nadie habia revisado era la **secuencia**.
  `TUNNEL_START` tenia dos emisores para el mismo tunel —`extract_sql` y `open_tunnel`,
  con campos distintos cada uno—, asi que medir `TUNNEL_START` -> `TUNNEL_READY` para
  sacar latencia arrancaba el cronometro en el evento equivocado. Se conservo el de
  `tunnel.py`, que es el unico que conoce el puerto local, y `alias` y `redshift_dbname`
  se mudaron alla para no perderlos.

- **El `UserWarning` de pandas ya no se filtra a consola.** `pd.read_sql` sobre una
  conexion de `psycopg2` avisaba "pandas only supports SQLAlchemy connectable" en cada
  extraccion, por CLI y por API, y tambien al correr la suite. No indicaba nada malo —la
  ruta DBAPI2 funciona y es la que esta libreria eligio a proposito, para no arrastrar
  SQLAlchemy— pero ensuciaba la salida del host y entrenaba a ignorar warnings. Se
  silencia solo ese mensaje y solo en esa llamada, con `warnings.catch_warnings()`, que
  restaura el estado al salir: la configuracion de warnings del host no se toca.

- **Los errores de uso de la linea de comandos salen con 64, no con 2** (F4). click sale
  con 2 por su cuenta ante un flag mal escrito, un argumento obligatorio faltante o un
  subcomando inexistente, y 2 es el codigo que el ecosistema le asigno a los errores de
  configuracion. Un runner que tratara el 2 como "revisa el `.env`" se equivocaba ante
  cualquier typo en el comando; F4 promete codigos estables y uno que significa dos cosas
  no lo es. Se agrega `main()` como entry point, que separa las dos cosas sin tocar
  estado global de click, mas el 130 para Ctrl+C. Ya estaba resuelto asi en
  `mongo_extractor`.

  **Las clases de error se resuelven desde la API publica de typer**, no desde el modulo
  privado `typer._click.exceptions`. Hace falta resolverlas en runtime porque desde
  typer 0.27 las excepciones del click vendorizado son clases DISTINTAS de las del
  paquete `click`, asi que un `except click.UsageError` fijo no matchea. Pero ese modulo
  privado se mueve entre versiones de parche:

  ```text
  typer 0.25.1   typer.Abort -> click.exceptions.Abort
  typer 0.27.1   typer.Abort -> typer._click.exceptions.Abort
  typer 0.27.2   typer.Abort -> typer.exceptions.Abort   (ya no esta en _click)
  ```

  Lo estable son `typer.Abort` y `typer.BadParameter`, cuyo MRO pasa por `UsageError` y
  `ClickException` vivan donde vivan. Verificado contra typer 0.25.1 y 0.27.2.

### Agrega

- **`params` en `extract_sql`**, para enlazar valores en vez de interpolarlos. Hasta
  ahora toda consulta con valores variables se armaba con `format` o f-strings; con
  fechas generadas en el codigo es inofensivo, pero deja de serlo en cuanto un valor
  venga de fuera —un filtro de un dashboard, un argumento de linea de comandos—, porque
  entonces es inyeccion de SQL.

  ```python
  extract_sql(
      "select * from ventas where ruta_id = %(ruta)s and fecha >= %(desde)s",
      params={"ruta": ruta_id, "desde": "2026-01-01"},
      alias="prod",
  )
  ```

  El marcador es `%(nombre)s`, el nativo de psycopg2, y no `:nombre` como la referencia,
  que usa SQLAlchemy. No se traduce de uno al otro a proposito: en Redshift `::` es el
  operador de cast y sale en casi cualquier query real, asi que un traductor tendria que
  distinguirlo del cast y de los `:` dentro de cadenas literales.

  **No cambia nada para quien no lo use.** Con `params=None` —el default— el argumento
  ni siquiera se le pasa a `pd.read_sql`: psycopg2 solo interpreta `%` cuando recibe
  parametros, asi que un SQL con `%` literales (`like '%rabbit%'`, `to_char(x, '%Y')`)
  sigue funcionando igual. Hay tests que lo fijan.

- `tests/test_cli.py`: 66 tests de `cli.py`, que estaba en cero cobertura pese a ser la
  superficie que se usa a mano y la que corre en tareas programadas. Cubre `apply_limit`,
  `first_keyword`, `read_sql`, `strip_trailing_semicolons`, `is_connection_error`,
  `execute_with_retries`, `print_result` y los codigos de salida del entry point real.

- `tests/test_params.py`: 9 tests del enlace de parametros, incluido que un valor
  malicioso no se convierte en SQL y que los `%` literales siguen intactos.

### Interno

- **Se quito el `platform = "win32"` de `[tool.mypy]`.** Existia para tapar cuatro
  errores `attr-defined` sobre `winreg` al typechequear en Linux, que es lo que hace el
  CI, y era la razon de que estuviera en rojo desde su primera corrida. El arreglo de
  fondo ya esta en las cuatro librerias del ecosistema: `secret_loader` guarda el import
  con `sys.platform != "win32"` en vez de `os.name != "nt"`, que mypy si entiende como
  estrechamiento de plataforma y que en runtime hace exactamente lo mismo. Sin el
  workaround, el typecheck vuelve a cubrir el codigo especifico de Windows en vez de
  darlo por bueno. Verificado en las dos plataformas.

### Rompe (solo a quien consuma eventos o codigos de salida)

| Cambio | Que pasa si no se edita |
|---|---|
| El aviso de "guardado activado" pasa de `QUERY_START` a `SAVE_CONFIGURED` | Un filtro por `QUERY_START` deja de ver ese evento. Es lo correcto: no era el inicio de una consulta |
| `TUNNEL_START` se emite una vez, no dos | Un contador que compensara la duplicacion ahora cuenta de menos |
| Los errores de uso salen con 64 en vez de 2 | Un script que trataba el 2 como "error de configuracion" ahora tiene que cubrir tambien el 64. A cambio, el 2 por fin significa una sola cosa |
| `[project.scripts]` apunta a `cli:main` y no a `cli:app` | Nada para quien usa el comando `redshift-extractor`. Quien invoque `python -c "from redshift_extractor.cli import app; app()"` se salta la separacion de codigos |

`SAVE_CONFIGURED` esta en `events.KNOWN_EVENTS` y trae `alias`, `save_dir`, `base_name`,
`save_csv` y `save_parquet`. Nada de esto afecta al CLI, que solo imprime los eventos.

`open_tunnel` acepta un `alias=` keyword-only opcional, para poder emitirlo en
`TUNNEL_START`. `open_tunnel(ssh, rs)` sigue construyendo igual.

## 0.3.0 - 2026-08-27

Ronda de homologacion contra `ESTANDAR.md` del ecosistema. Cierra los once pendientes que
tenia esta libreria, mas el retiro de las formas viejas, el test de divergencia de las
copias duplicadas y el shim de credenciales.

0.2.0 nunca se publico: los cambios de firma que iban a entregarse con la forma vieja
aceptada terminaron entregandose sin ella, asi que el salto es de 0.1.0 a 0.3.0.

### Rompe

No hay `DeprecationWarning` en ningun caso: lo que cambio truena.

| Cambio | Que pasa si no se edita | Como encontrarlo |
|---|---|---|
| El alias como primer posicional de `extract_sql` | `TypeError` | `findstr /s /n /c:"extract_sql(" *.py` |
| `db=` en `extract_sql`, `ping` y `config.resolve` | `TypeError` | el mismo grep |
| `list_databases()`, `list_available_databases()` | `AttributeError` o `ImportError` | `findstr /s /n /c:"list_databases" *.py` |
| `--db` en el CLI | `No such option: --db`, exit 2 | grep en `.ps1`, `.bat` y tareas programadas |
| `redshift_extractor.credentials` | `ModuleNotFoundError` | `findstr /s /n /c:"credentials import" *.py` |
| `pyarrow` fuera de las dependencias duras | `ImportError` con el comando de instalacion | `save_parquet=True` en el codigo |
| La host key del bastion se verifica siempre | `TunnelHostKeyError` hasta registrarlo | paso 4 de `docs/onboarding.md` |

La forma canonica, identica a la de `postgres_local_client`:

```python
extract_sql("select 1")                    # alias = DEFAULT_ALIAS
extract_sql("select 1", alias="prod")
extract_sql(query_file="q.sql", alias="prod")
```

**El unico caso que no truena y hay que buscar a mano:** `extract_sql("prod")` con un solo
posicional. Antes ese posicional era el alias; ahora es el SQL, asi que la llamada llega
al cluster con `prod` como consulta y falla como error de SQL, no de la libreria. Es la
razon por la que el retiro va en un salto de version mayor.

Dos cambios mas, sin forma vieja a proposito, para que nadie copie el nombre equivocado:
el campo de eventos `db=` pasa a `alias=`, y `ping()` devuelve la clave `"alias"`.

La verificacion de la host key es la unica ruptura sin forma vieja **posible**: aceptar
cualquier host key es la vulnerabilidad que el criterio I1 cierra.

### Agrega

- **Tunel endurecido** en el alcance que fijo la decision DE-4: host key verificada
  siempre por fingerprint declarado o por `known_hosts` (`AutoAddPolicy` prohibido),
  cierre garantizado ante `Ctrl+C`, `SIGTERM` y fin de proceso, health check a nivel de
  protocolo y errores tipados por modo de falla. No se porta el reuso por destino ni
  `tunnel_status`: esta libreria abre un tunel por operacion.
- **`errors.py`** con raiz `RedshiftExtractorError`. Hereda de `RuntimeError`, y
  `ConfigError` tambien de `ValueError`, para que los `except` que ya tenian los hosts
  sigan atrapando.
- **`events.py`** con `StatusEvent`, `OnEvent`, `emit()` y catalogo. Un `on_event` que
  lanza no tumba la operacion.
- **`ping()`** y `redshift-extractor ping`: verifica tunel, cluster y credenciales sin
  lanzar una consulta de negocio, reportando base y usuario tal como los ve el servidor.
- **`redshift-extractor fingerprint`**: muestra la host key que presenta el bastion.
- **`DEFAULT_ALIAS`** en el env, y campos nuevos del tunel (`SSH_LOCAL_PORT`,
  `SSH_HOST_FINGERPRINT`, `SSH_KNOWN_HOSTS_PATH`, `SSH_CONNECT_TIMEOUT_S`,
  `SSH_KEEPALIVE_S`, `SSH_COMPRESSION`).
- **`py.typed`**, para que los hosts vean los tipos que ya estaban escritos.
- **Extra `parquet`**, y codigos de salida del CLI documentados (config 2, tunel 3).
- **`docs/onboarding.md`**, **`docs/compatibilidad.md`** y este changelog.
- **166 tests**, de 13. Servidor SSH en proceso y doble del cluster, suite de tunel,
  canon del alias, convivencia y divergencia de las copias duplicadas. 162 corren sin
  infraestructura.
- **CI en matriz 3.10 y 3.13**, en todas las ramas.

### Arregla

- **El BOM del `.env` ya no se acepta en silencio** (decision DE-1). Antes se leia con
  `utf-8-sig`; el sintoma que producia, "la primera variable se lee vacia", es de los mas
  caros de diagnosticar en Windows.
- **`LOG_LEVEL` exige prefijo propio** para el override del proceso
  (`REDSHIFT_EXTRACTOR_LOG_LEVEL`). Un `LOG_LEVEL` suelto del host ya no se consume.
- **`basicConfig` desaparecio del paquete.** La libreria configura solo su propio logger
  y el de paramiko queda aislado con `propagate=False`.
- **El deadlock de `sshtunnel` 0.4.0.** Con una llave rechazada, el forward server
  quedaba sin su hilo `serve_forever` y el `stop()` esperaba para siempre: colgaba el
  proceso en vez de dar un error.
- **Una clave `REDSHIFT__*` mal formada es error**, no se ignora. El caso tipico es el
  campo en minusculas, donde el usuario cree que configuro el host y no configuro nada.
- **Pines exactos en las dependencias.** `sshtunnel==0.4.0` y `python-dotenv==1.0.1`
  pasan a rangos `>=`, y el piso de pandas baja de 2.2.3 a 2.0. Un pin exacto en una
  libreria interna se vuelve un conflicto irresoluble en el proyecto host.
- **El CI, que nunca habia estado verde.** Las 13 corridas del historial fallaron, todas
  en mypy, desde la primera, y `docs/pendientes.md` afirmaba lo contrario. La causa: la
  copia compartida de `secret_loader.py` importa `winreg` bajo la guarda
  `os.name != "nt"` y mypy estrecha por `sys.platform`. Destrabado con
  `platform = "win32"`; el arreglo de fondo va en las cuatro librerias a la vez.

### Verificacion

`ping()` y `extract_sql()` probados contra el bastion y el cluster real. CI verde en 3.10
y 3.13. El test de divergencia se valido con una prueba de mutacion: se rompieron a
proposito `secret_loader.py`, el restore de las globales de `sshtunnel`, dos constantes de
modulo y un doble de test, y las cazo todas.

Lo que queda anotado, con su senal y su costo, en `docs/pendientes.md`.
