# Compatibilidad y convivencia

Este documento responde una sola pregunta: **que garantiza que un proyecto host pueda
instalar esta libreria junto a las otras del ecosistema sin que se pisen.**

Cubre H10 del `ESTANDAR.md`. Todo lo de aqui se verifica dentro de este repo, sin
tener instaladas las hermanas: `tests/test_convivencia.py`.

Ultima revision: 2026-08-27.

---

## 1. Politica de dependencias

Rangos con `>=` y **sin techo**. Un techo preventivo en una libreria interna se vuelve
un conflicto irresoluble en el host: basta que otra del ecosistema pida algo distinto
para que no haya resolucion posible, y el diagnostico ocurre en el proyecto del
usuario, donde es mas caro.

Los pisos son el **minimo real probado**, no el mas alto disponible. Un piso alto
excluye tanto como un techo bajo.

| Dependencia | Declarado | Por que ese piso |
|---|---|---|
| `pandas` | `>=2.0` | Es lo que pide la referencia. Antes decia `>=2.2.3`, que forzaba la version de pandas en cualquier host |
| `psycopg2-binary` | `>=2.9` | Version en la que la rueda binaria es estable en Windows |
| `sshtunnel` | `>=0.4` | 0.4.0 es su ultima release (2021). Antes estaba pineado exacto |
| `paramiko` | `>=2.7.2,<4` | Unico techo del proyecto. Ver seccion 2 |
| `keyring` | `>=24` | Lectura de KeyringManager |
| `python-dotenv` | `>=1.0` | `dotenv_values` con `encoding`. Antes estaba pineado exacto |
| `typer` | `>=0.12` | CLI |
| `pyarrow` | extra `parquet`, `>=12` | Ver seccion 3 |

Hasta 0.1.0 esta libreria declaraba `sshtunnel==0.4.0` y `python-dotenv==1.0.1`. No
truena porque la referencia declara rangos laxos que aceptan esos pines, pero la
asimetria era fragil: **la referencia se estaba acomodando a esta libreria**, no al
reves.

No se re-declaran transitivas: solo lo que el codigo importa directamente (B5).

### Que garantiza esta politica y que no

Conviene separarlo, porque son dos preguntas distintas y solo una esta verificada aqui.

| Pregunta | Estado | Donde se verifica |
|---|---|---|
| `pip` puede instalar esta libreria junto a otra del ecosistema sin `ResolutionImpossible`? | **No verificado con evidencia.** Los rangos `>=` quitan la causa conocida, pero nadie ha corrido la instalacion conjunta con estos rangos | Habria que correrlo. Ver abajo |
| Las dos, ya instaladas, conviven en ejecucion sin pisarse? | **Verificado**, y por construccion | `tests/test_convivencia.py`, sin necesidad de las hermanas |

Lo segundo es lo que importa en el dia a dia y es lo que esta cubierto: ninguna de las
dos escribe en `os.environ`, ninguna toca el root logger, cada una lee solo su `.env`,
el puerto local es efimero y cada una cierra unicamente el tunel que abrio. Eso se
comprueba con greps literales y con estado antes/despues, dentro de este repo.

Lo primero es una propiedad del **conjunto**, no de esta libreria, y no se puede afirmar
desde aqui: depende de lo que declaren las otras cinco el dia que un host las instale
junto a esta. Lo que si se puede afirmar es que esta libreria dejo de ser el problema:

- Hasta 0.1.0 declaraba `sshtunnel==0.4.0` y `python-dotenv==1.0.1`. Dos pines exactos.
  Funcionaban solo porque la referencia declaraba rangos laxos que los aceptaban: **la
  referencia se estaba acomodando a esta libreria**, no al reves. Con dos pines exactos
  incompatibles entre hermanas no hay resolucion posible y el error aparece en el
  proyecto del host, donde es mas caro diagnosticarlo.
- Desde 0.3.0 el unico techo es `paramiko<4`, con su incompatibilidad concreta
  documentada, y la referencia declara el mismo. Un techo que las dos comparten no puede
  hacerlas irreconciliables entre si.

**Como conseguir la evidencia, si algun dia hace falta:**

```powershell
pip install --dry-run --ignore-installed --report reporte.json ^
  ./redshift_extractor ./postgresql_extractor_uploader
```

por cada par y luego las seis juntas. La referencia tiene el ejercicio hecho para sus
pares en su `docs/compatibilidad.md` seccion 1, con la tabla de versiones que gana el
resolvedor, y el test que lo automatiza detras de una variable de entorno porque pega a
la red y tarda minutos.

**Por que no se corre ahora:** la evidencia caduca. Cada release de `pandas`, `paramiko`
o `typer` cambia lo que el resolvedor elige, asi que un reporte guardado hoy describe un
estado que ya no es cierto en un mes. Tiene valor cuando hay un host concreto que
instala dos, y ahi la corrida es de una tarde. Hoy no hay ninguno: la busqueda en
`Funciones/` no encontro ningun proyecto que importe dos de estas librerias.

---

## 2. El unico techo: `paramiko<4`

`sshtunnel` 0.4.0 referencia `paramiko.DSSKey` dentro de `get_keys()`, que se llama en
**cada** construccion del forwarder. paramiko elimino `DSSKey` en 4.0.0, asi que con
`paramiko>=4` el tunel truena con:

```
AttributeError: module 'paramiko' has no attribute 'DSSKey'
```

Verificado el 2026-08-12 con paramiko 4.0.0 y 5.0.0. Ultima compatible: 3.5.1.

El techo lleva comentario en `pyproject.toml` con la incompatibilidad concreta, la
version que falla y la fecha (B2). Se quita cuando salga una `sshtunnel` que soporte
paramiko 4.

El piso es `2.7.2` y no `3.5.0` a proposito: `_load_private_key` tiene una rama para
paramiko < 3.2, que no tiene `PKey.from_path`.

---

## 3. `pyarrow` en un extra

`pyarrow` son ~40 MB que solo hacen falta para `save_parquet=True`. Estaba en las
dependencias duras, asi que se instalaba en todo host que solo queria leer SQL.

Desde 0.3.0 vive en `[project.optional-dependencies] parquet`. Pedir Parquet sin el
extra da un `ImportError` que dice el comando exacto:

```
pip install "redshift-extractor[parquet]"
```

**Esto rompe a quien dependia de que viniera incluido.** Va anunciado en el README y en
`CHANGELOG.md`.

---

## 4. Aislamiento del `.env`

Esta libreria lee **solo** `.env.redshift_extractor` y **no escribe en `os.environ`**.

El bug que eso evita es concreto. Con `load_dotenv()` -que era lo que se usaba antes
de 2026-08-12- el archivo se copia al entorno del proceso. Si un host instala dos
librerias del ecosistema y ambas definen una variable con el mismo nombre plano
(`SSH_HOST`, `SSH_PORT`, `SSH_USER`, `SSH_PKEY_PATH`, `LOG_LEVEL`, `OUTPUT_DIR`), la
primera en cargar gana -python-dotenv usa `override=False` por defecto- y la segunda
se queda **en silencio** con los valores de la otra. Dos librerias que tunelean a
bastiones distintos: la segunda intentaria conectarse al bastion de la primera.

El bug solo aparece en el host que instala dos, nunca en el venv de desarrollo de cada
libreria. Por eso se verifica por grep literal sobre el paquete (H1, H2).

| Que | Como |
|---|---|
| Localizacion | `REDSHIFT_EXTRACTOR_ENV_FILE`, o busqueda hacia arriba desde el paquete |
| Lectura | `dotenv_values(..., encoding="utf-8")`, devuelve un dict |
| BOM | **Falla** con mensaje explicito. Nunca `utf-8-sig` (DE-1) |
| Overrides del proceso | Solo con prefijo propio: `REDSHIFT_EXTRACTOR_LOG_LEVEL` |

Un `LOG_LEVEL` suelto en el entorno del proceso **no** se consume: pertenece al host o
a otra libreria (C3).

---

## 5. Sin mutacion de estado global

| Global | Que hace esta libreria |
|---|---|
| `os.environ` | No escribe. Solo lee `REDSHIFT_EXTRACTOR_*` y los nombres de variables que apuntan a credenciales |
| Root logger | No lo toca. Ni `level`, ni `handlers`, ni `basicConfig()` en ningun modulo |
| Logger propio | `redshift_extractor`, con `NullHandler`. El CLI le agrega un handler a consola solo a el |
| Logger de paramiko/sshtunnel | Logger propio `redshift_extractor.ssh` con `propagate=False`. No se silencia el logger global de paramiko |
| `warnings.filterwarnings` | No se modifica |
| `logging.captureWarnings` | `sshtunnel.create_logger()` la enciende. Se toma snapshot y se restaura |
| `SIGTERM` | El handler se **encadena** al previo, no lo reemplaza |
| `SIGINT` | No se toca: su default levanta `KeyboardInterrupt`, que es lo que hace correr `atexit` |
| `atexit` | Aditivo. El handler nunca lanza |

Las tres mutaciones de `sshtunnel.create_logger()` -`logging.captureWarnings(True)`,
handlers en `py.warnings` y handlers en el logger global `paramiko.transport`- quedan
restauradas al salir de cada llamada a `sshtunnel` (H7). El restore nunca lanza.

---

## 6. Convivencia de tuneles

| Regla | Detalle |
|---|---|
| Puerto local efimero por default | `SSH_LOCAL_PORT=0`. Dos librerias con puerto fijo colisionan (H5) |
| Solo cierra lo que abrio | El registro interno guarda `owned=True`; un tunel ajeno nunca se toca (H6) |
| Un tunel por proceso | Esta libreria no reusa por destino (I3) ni expone `tunnel_status` (I7): DE-4 los dejo fuera porque abre un tunel por operacion. La referencia si los tiene, porque escribe y abre varias veces |
| No adopta tuneles externos | Un puerto local ocupado da `TunnelBindError` en vez de reusarse a ciegas: si del otro lado hubiera otro tunel, la conexion funcionaria pero apuntaria al cluster equivocado |

---

## 7. Lo que NO esta verificado

- **Resolucion conjunta en un host real con las seis librerias.** La referencia
  documenta la suya en su `docs/compatibilidad.md` seccion 1, y esta libreria ya no
  aporta pines exactos, que era lo que hacia fragil esa resolucion. Falta repetir el
  ejercicio con estos rangos.
- ~~Divergencia de las copias duplicadas.~~ **Cubierta** por
  `tests/test_divergencia.py` desde el 2026-08-27: compara `secret_loader.py` completo,
  la maquinaria de `events.py`, las piezas del tunel que DE-4 mando portar y los dobles
  de test contra los repos hermanos que esten en la maquina, y se salta con mensaje si
  no estan. Ver la seccion 8.

---

## 8. Divergencia de las copias duplicadas

El ecosistema duplica a proposito. El costo de esa decision es que las copias pueden
divergir en silencio, y `tests/test_divergencia.py` es lo que lo caza. Compara contra
los repos hermanos que esten en el directorio de al lado y se salta con mensaje si no
estan, asi que el CI -donde no hay hermanas- no depende de ellas.

| Archivo | Rigor de la comparacion | Por que ese rigor |
|---|---|---|
| `secret_loader.py` | Texto identico, modulo el nombre del paquete | Hoy es identico a las tres hermanas. Es el archivo que resuelve credenciales: si alguien arregla un formato aqui y no alla, el bug queda vivo en tres repos |
| `events.py` | Mismo codigo (AST sin docstrings) en `emit`, `redact`, `register_secret`, `clear_secrets`, mas sus constantes de modulo | La maquinaria es copia; el catalogo `KNOWN_EVENTS` divergio a proposito (MAYUSCULAS, ver `docs/pendientes.md`) |
| `tunnel.py` | Mismo codigo en las once piezas que DE-4 mando portar, mas las constantes del protocolo | El archivo es propio por D1. Lo homologado es el nivel de endurecimiento, no el archivo |
| `tests/sshserver.py`, `tests/fakepg.py` | Texto identico | No importan el paquete, asi que no hay nada que normalizar mas que el fin de linea |

Tres decisiones de diseno del test, cada una porque la alternativa daba un falso
positivo o un falso negativo:

1. **Se compara el AST sin docstrings, no el texto.** Los mensajes de esta libreria
   hablan de "bastion" donde la referencia dice "VM", y el ancho de los docstrings
   difiere. Comparar texto marcaria eso como divergencia y el test se volveria ruido
   que alguien borra.
2. **Las constantes de modulo se comparan aparte.** Bajar `_MIN_SECRET_LEN` o cambiar
   el codigo del `SSLRequest` no altera el AST de ninguna funcion: comparando solo
   funciones, esas dos mutaciones pasaban limpias. Se verifico con una prueba de
   mutacion.
3. **El fin de linea se normaliza.** Esta maquina tiene `core.autocrlf` activo, asi que
   dos clones del mismo contenido pueden quedar uno con CRLF y otro con LF.

El test tambien afirma la contraparte: que `KNOWN_EVENTS` sigue en MAYUSCULAS aqui y en
minusculas en la referencia. Si el ecosistema homologa el casing, el test falla y
obliga a decidir, en vez de dejar una nota vieja en el doc.

Y afirma que I3 e I7 **no** aparecieron en el tunel: si alguien agrega reuso por
destino sin reabrir DE-4, eso es un accidente, no una mejora.
