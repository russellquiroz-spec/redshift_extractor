# Changelog

Este archivo empieza en 0.3.0. Para lo anterior, el historico de commits: hasta 0.1.0 el
proyecto no llevaba changelog.

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
