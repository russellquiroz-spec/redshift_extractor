# Arquitectura de Credenciales por Variable de Sistema

## Objetivo

Definir la logica de resolucion de credenciales del proyecto para conexiones Redshift por alias, separando:

- configuracion no sensible en `.env.redshift_extractor`
- secretos en variables de sistema

Esta guia esta escrita como documentacion de arquitectura del proyecto y tambien sirve como patron para otras funciones internas.

## Principios

- El `.env.redshift_extractor` no debe guardar credenciales.
- Cada alias define solo `HOST`, `PORT`, `DBNAME` y el nombre de la variable de sistema donde viven sus credenciales.
- La variable indicada en `CREDENTIALS_ENV` es la fuente de verdad para `user/password`.
- El parser debe ser tolerante a formatos de secreto comunes en entornos Windows y automatizaciones.

## Estructura recomendada

```env
REDSHIFT__prod__HOST=your-prod-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__prod__PORT=5439
REDSHIFT__prod__DBNAME=analytics
REDSHIFT__prod__CREDENTIALS_ENV=REDSHIFT_PROD_CREDENTIALS

REDSHIFT__dev__HOST=your-dev-cluster.xxxxxx.region.redshift.amazonaws.com
REDSHIFT__dev__PORT=5439
REDSHIFT__dev__DBNAME=analytics_dev
REDSHIFT__dev__CREDENTIALS_ENV=REDSHIFT_DEV_CREDENTIALS
```

## Flujo de resolucion

Cuando se llama `load_config()`:

1. Se localiza `.env.redshift_extractor`.
2. Se cargan las variables `REDSHIFT__<ALIAS>__<FIELD>`.
3. Para cada alias se leen `HOST`, `PORT`, `DBNAME` y `CREDENTIALS_ENV`.
4. Si `CREDENTIALS_ENV` existe, primero se intenta resolver en `KeyringManager`:
   - se busca `%APPDATA%\KeyringManager\credentials.json`
   - se localiza la entrada cuyo `env_var` coincide con `CREDENTIALS_ENV`
   - se toma `usuario`
   - se recupera el password con `keyring.get_password(service, usuario)`
5. Si no existe una entrada en `KeyringManager`, entonces:
   - se busca esa variable en `os.environ`
   - si no aparece y el sistema es Windows, se intenta leerla del registro
6. El valor encontrado se parsea hasta recuperar `user/password`.
7. Con eso se construye `RedshiftConfig`.

## Fuente de verdad

Si un alias tiene `CREDENTIALS_ENV`, esa referencia es la fuente de verdad para `user/password`.

La resolucion puede venir de:

- una entrada en `KeyringManager`
- el valor directo de una variable de sistema

Eso implica:

- no depender de `USER` o `PASSWORD` en el `.env`
- evitar configuraciones duplicadas o inconsistentes
- poder mover credenciales entre proyectos sin editar codigo ni archivos locales

## Formatos soportados

### 1. JSON simple

```text
{"user":"db_user","password":"db_password"}
```

### 2. JSON con campos extra

```text
{"user":"db_user","password":"db_password","comment":"prod credentials"}
```

### 3. JSON anidado

```text
{"metadata":{"owner":"data-team"},"credentials":{"UserName":"db_user","Password":"db_password"}}
```

### 4. JSON serializado como string

```text
"{\"user\":\"db_user\",\"password\":\"db_password\"}"
```

### 5. Wrapper con comillas simples

```text
'{"user":"db_user","password":"db_password"}'
```

### 6. Pares tipo key-value

```text
USER=db_user;PASSWORD=db_password
```

### 7. Formato delimitado

```text
db_user:db_password
```

## Consideraciones de Windows

En Windows pueden existir diferencias entre:

- lo que ya esta guardado como variable de sistema
- lo que ve el proceso actual en `os.environ`
- lo que realmente llega al codigo
- lo que esta registrado en `KeyringManager`

Por eso el proyecto tambien intenta leer del registro si la variable no aparece en el proceso actual:

- `HKEY_CURRENT_USER\Environment`
- `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`

Y si existe `KeyringManager`, el proyecto lo usa como primera opcion para resolver credenciales.

## Componentes clave

### `src/redshift_extractor/secret_loader.py`

Funciones principales:

- `resolve_secret_reference`
- `resolve_secret_reference_from_keyring_manager`
- `read_system_env_value`
- `read_windows_env_value_from_registry`
- `parse_credentials_secret`

Responsabilidades:

- resolver una referencia a secreto en un modulo reusable para otras funciones
- resolver credenciales desde `KeyringManager` cuando exista una entrada asociada al alias
- parsear `user/password` desde formatos estructurados

### `src/redshift_extractor/config.py`

Responsabilidades:

- localizar el `.env` propio del proyecto
- descubrir aliases Redshift
- delegar la resolucion de `user/password` al helper comun

### `.env.example`

Muestra el patron recomendado de configuracion sin exponer secretos.

### `tests/test_config.py`

Cubre los formatos soportados, la resolucion via `KeyringManager` y el fallback de Windows para asegurar que la logica se mantenga estable.

## Diagnostico rapido

### Ver el valor crudo que esta leyendo el proyecto

```powershell
python -c "from redshift_extractor.config import _get_env_value; print(repr(_get_env_value('REDSHIFT_PROD_CREDENTIALS')))"
```

### Ver el valor ya parseado por alias

```powershell
python -c "from redshift_extractor.config import load_config; _, rs_map = load_config(); rs = rs_map['prod']; print(rs.user); print(repr(rs.password))"
```

### Ver si el proceso esta usando el `.env` esperado

```powershell
python -c "from redshift_extractor.config import _find_env_file; print(_find_env_file())"
```

## Patron reusable para otras funciones

Si otra funcion necesita la misma arquitectura:

1. Define un `.env` propio solo para configuracion no sensible.
2. Guarda credenciales en variables de sistema.
3. Si el equipo ya usa `KeyringManager`, aprovecha ese flujo como primera opcion.
4. Usa una variable tipo `CREDENTIALS_ENV` o `SECRET_ENV` por alias o entorno.
5. Centraliza la lectura y el parseo del secreto en un helper comun, por ejemplo `secret_loader.py`.
6. Soporta formatos estructurados y diferencias de Windows desde el inicio.
7. Agrega pruebas del parser antes de usarlo en produccion.

## Checklist de implementacion

- Identificar donde hoy se leen `USER` y `PASSWORD`.
- Mover la credencial a una variable de sistema.
- Si existe `KeyringManager`, registrar ahi `usuario`, `service` y `env_var`.
- Dejar el `.env` solo con host, puerto, db y nombre de variable.
- Hacer que la variable de sistema sea la fuente de verdad.
- Validar el flujo en terminal nueva, VS Code y Jupyter si aplica.
- Probar especificamente en Windows.
