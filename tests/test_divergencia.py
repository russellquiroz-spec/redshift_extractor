"""
Divergencia de las copias duplicadas (D5).

El ecosistema duplica a proposito: `secret_loader.py`, el modulo de eventos, el
nucleo del tunel y los dobles de test son **copias**, no dependencias, porque un
paquete comun crearia el acoplamiento que la separacion en repos evita. El costo de
esa decision es que las copias pueden divergir en silencio, y eso es lo que estos
tests cazan.

Todo se salta con mensaje si la hermana no esta en esta maquina: la suite tiene que
correr sin ella.

Lo que se compara y con que rigor:

| Archivo | Rigor | Por que |
|---|---|---|
| `secret_loader.py` | texto identico, modulo el nombre del paquete | Es la copia estricta: hoy es identica a las tres hermanas |
| `events.py` | mismo codigo (AST sin docstrings), salvo `KNOWN_EVENTS` | El catalogo de eventos divergio a proposito (MAYUSCULAS, E8); la maquinaria no |
| `tunnel.py` | mismo codigo en las piezas que DE-4 mando portar | El archivo es propio por D1; lo homologado es el nivel de endurecimiento |
| `tests/sshserver.py`, `tests/fakepg.py` | texto identico | No importan el paquete, asi que solo se normaliza el fin de linea |
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path
from typing import Dict, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAQUETE = REPO_ROOT / "src" / "redshift_extractor"
FUNCIONES_DIR = REPO_ROOT.parent

#: (directorio del repo hermano, nombre de su paquete). La referencia va primero.
HERMANAS: Tuple[Tuple[str, str], ...] = (
    ("postgresql_extractor_uploader", "postgres_local_client"),
    ("mongo_extractor", "mongo_extractor"),
    ("netsuite_extractor", "netsuite_extractor"),
)

REFERENCIA = HERMANAS[0]

#: Piezas del tunel que DE-4 mando portar y cuyo codigo no debe derivar. Son las
#: sutiles: el restore de las globales de sshtunnel (H7) y el rodeo del deadlock de
#: sshtunnel en fallo de auth. El resto del archivo esta adaptado a proposito
#: (mensajes propios, sin reuso por destino).
TUNEL_SIN_DERIVA = (
    "_no_logging_side_effects",
    "_abort_forwarder",
    "_shutdown_forwarder",
    "_register_cleanup",
    "_preflight",
    "fetch_remote_host_key",
    "resolve_host_key",
    "fingerprint",
    "port_is_free",
    "_known_hosts_path",
    "_host_key_names",
)

#: Maquinaria de eventos que debe ser identica. `KNOWN_EVENTS` queda fuera: el
#: catalogo de esta libreria esta en MAYUSCULAS a proposito.
EVENTOS_SIN_DERIVA = ("emit", "redact", "register_secret", "clear_secrets")

#: Constantes de modulo que tambien tienen que coincidir. Van aparte porque una
#: mutacion aqui —bajar `_MIN_SECRET_LEN`, cambiar el codigo del SSLRequest— no altera
#: el AST de ninguna funcion y pasaria limpia si solo se compararan funciones.
EVENTOS_CONSTANTES = (
    "StatusEvent",
    "OnEvent",
    "_LEVEL_TO_LOGGING",
    "_REDACTED",
    "_MIN_SECRET_LEN",
)
TUNEL_CONSTANTES = (
    "_LOCALHOST",
    "_PROBE_TIMEOUT_S",
    "_PG_SSL_REQUEST",
    "_PG_SSL_REPLIES",
)


def _ruta_hermana(repo: str, paquete: str, archivo: str) -> Optional[Path]:
    ruta = FUNCIONES_DIR / repo / "src" / paquete / archivo
    return ruta if ruta.exists() else None


def _sin_eol(texto: str) -> str:
    """
    Deja el fin de linea fuera de la comparacion.

    Hace falta porque esta maquina tiene `core.autocrlf` activo: dos clones del mismo
    contenido pueden quedar uno con CRLF y otro con LF, y comparar bytes crudos
    reportaria divergencia por algo que git decidio. Un test que grita en falso es un
    test que alguien borra.
    """
    return texto.replace("\r\n", "\n").replace("\r", "\n")


def _normalizar(texto: str, paquete: str) -> str:
    """Deja el nombre del paquete y el fin de linea fuera de la comparacion."""
    return _sin_eol(texto).replace(paquete, "PAQUETE")


def _sin_docstrings(nodo: ast.AST) -> ast.AST:
    for sub in ast.walk(nodo):
        cuerpo = getattr(sub, "body", None)
        if not isinstance(cuerpo, list) or not cuerpo:
            continue
        primero = cuerpo[0]
        if (
            isinstance(primero, ast.Expr)
            and isinstance(primero.value, ast.Constant)
            and isinstance(primero.value.value, str)
        ):
            sub.body = cuerpo[1:]  # type: ignore[attr-defined]
    return nodo


def _codigo_por_funcion(path: Path, paquete: str) -> Dict[str, str]:
    """
    Devuelve {nombre: AST sin docstrings} de las funciones de nivel superior.

    Se compara el AST y no el texto para que los comentarios y el ancho de los
    docstrings se puedan adaptar —el mensaje de error de esta libreria habla de
    "bastion", no de "VM"— sin que eso cuente como divergencia. Lo que no puede
    cambiar es el codigo.
    """
    fuente = _normalizar(path.read_text(encoding="utf-8"), paquete)
    arbol = ast.parse(fuente)
    return {
        nodo.name: ast.dump(_sin_docstrings(nodo))
        for nodo in arbol.body
        if isinstance(nodo, ast.FunctionDef)
    }


def _constantes_de_modulo(path: Path, paquete: str) -> Dict[str, str]:
    """Devuelve {nombre: AST del valor} de las asignaciones de nivel superior."""
    fuente = _normalizar(path.read_text(encoding="utf-8"), paquete)
    valores: Dict[str, str] = {}
    for nodo in ast.parse(fuente).body:
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name):
                    valores[destino.id] = ast.dump(nodo.value)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            valores[nodo.target.id] = ast.dump(nodo.value) if nodo.value else "<sin valor>"
    return valores


def _comparar_constantes(
    archivo: str, nombres: tuple, mio_path: Path, suyo_path: Path, paquete: str, repo: str
) -> None:
    mio = _constantes_de_modulo(mio_path, "redshift_extractor")
    suyo = _constantes_de_modulo(suyo_path, paquete)

    faltantes = [nombre for nombre in nombres if nombre not in mio]
    assert not faltantes, f"la copia local de {archivo} perdio las constantes: {faltantes}"

    distintas = {
        nombre: (mio[nombre], suyo[nombre])
        for nombre in nombres
        if nombre in suyo and mio[nombre] != suyo[nombre]
    }
    assert not distintas, (
        f"estas constantes de {archivo} ya no coinciden con {repo}: "
        f"{sorted(distintas)}. Si el cambio es deliberado, documentalo y sacalo de la lista."
    )


def _hermanas_con(archivo: str) -> list:
    return [
        (repo, paquete)
        for repo, paquete in HERMANAS
        if _ruta_hermana(repo, paquete, archivo) is not None
    ]


# -----------------------------------------------------------------------------
# secret_loader.py: la copia estricta
# -----------------------------------------------------------------------------
def test_secret_loader_es_identico_al_de_las_hermanas():
    """
    D5: identico modulo el nombre del paquete y el fin de linea.

    Es el archivo mas delicado del ecosistema —resuelve credenciales— y hoy es
    identico en las cuatro librerias. Si alguien arregla un formato de secreto aqui y
    no en las hermanas, este test lo dice.
    """
    disponibles = _hermanas_con("secret_loader.py")
    if not disponibles:
        pytest.skip(
            "Ninguna hermana en esta maquina: la copia local no se pudo comparar. "
            "Se verifica igual en el repo de al lado cuando esta disponible."
        )

    mio = _normalizar(
        (PAQUETE / "secret_loader.py").read_text(encoding="utf-8"), "redshift_extractor"
    )
    divergen = {}
    for repo, paquete in disponibles:
        ruta = _ruta_hermana(repo, paquete, "secret_loader.py")
        assert ruta is not None
        suyo = _normalizar(ruta.read_text(encoding="utf-8"), paquete)
        if suyo != mio:
            diff = list(
                difflib.unified_diff(
                    mio.splitlines(), suyo.splitlines(), "local", repo, lineterm="", n=1
                )
            )
            divergen[repo] = "\n".join(diff[:40])

    assert not divergen, "secret_loader.py divergio:\n" + "\n".join(
        f"--- {repo}\n{texto}" for repo, texto in divergen.items()
    )


# -----------------------------------------------------------------------------
# events.py: la maquinaria, no el catalogo
# -----------------------------------------------------------------------------
def test_la_maquinaria_de_eventos_no_derivo_de_la_referencia():
    repo, paquete = REFERENCIA
    ruta = _ruta_hermana(repo, paquete, "events.py")
    if ruta is None:
        pytest.skip(f"{repo} no esta en esta maquina: events.py no se pudo comparar.")

    mio = _codigo_por_funcion(PAQUETE / "events.py", "redshift_extractor")
    suyo = _codigo_por_funcion(ruta, paquete)

    faltantes = [nombre for nombre in EVENTOS_SIN_DERIVA if nombre not in mio]
    assert not faltantes, f"la copia local de events.py perdio: {faltantes}"

    distintas = [
        nombre
        for nombre in EVENTOS_SIN_DERIVA
        if nombre in suyo and mio[nombre] != suyo[nombre]
    ]
    assert not distintas, (
        f"el codigo de estas funciones de events.py ya no coincide con {repo}: "
        f"{distintas}. Si el cambio es deliberado, documentalo en docs/pendientes.md "
        "y sacalo de EVENTOS_SIN_DERIVA."
    )

    _comparar_constantes(
        "events.py", EVENTOS_CONSTANTES, PAQUETE / "events.py", ruta, paquete, repo
    )


def test_el_catalogo_de_eventos_divergio_a_proposito():
    """
    Contraparte del test de arriba: la divergencia documentada se afirma, no se asume.

    Si algun dia el ecosistema homologa el casing, este test falla y obliga a decidir
    en vez de dejar la nota vieja en el doc.
    """
    from redshift_extractor.events import KNOWN_EVENTS

    assert all(nombre.isupper() for nombre in KNOWN_EVENTS)

    repo, paquete = REFERENCIA
    ruta = _ruta_hermana(repo, paquete, "events.py")
    if ruta is None:
        pytest.skip(f"{repo} no esta en esta maquina.")
    assert "config_loaded" in ruta.read_text(encoding="utf-8"), (
        "la referencia ya no usa minusculas: revisar si la divergencia de casing "
        "documentada en docs/pendientes.md sigue teniendo sentido."
    )


# -----------------------------------------------------------------------------
# tunnel.py: el nivel de endurecimiento
# -----------------------------------------------------------------------------
def test_las_piezas_portadas_del_tunel_no_derivaron():
    """
    DE-4: el archivo es propio (D1), pero lo que se porto no puede derivar.

    Cubre en particular el restore de las tres mutaciones globales de
    `sshtunnel.create_logger()` (H7) y el rodeo del deadlock: dos piezas que nadie
    revisa hasta que fallan y que son identicas en las dos librerias.
    """
    repo, paquete = REFERENCIA
    ruta = _ruta_hermana(repo, paquete, "tunnel.py")
    if ruta is None:
        pytest.skip(f"{repo} no esta en esta maquina: tunnel.py no se pudo comparar.")

    mio = _codigo_por_funcion(PAQUETE / "tunnel.py", "redshift_extractor")
    suyo = _codigo_por_funcion(ruta, paquete)

    faltantes = [nombre for nombre in TUNEL_SIN_DERIVA if nombre not in mio]
    assert not faltantes, (
        f"la copia local del tunel perdio piezas que DE-4 mando portar: {faltantes}"
    )

    distintas = [
        nombre
        for nombre in TUNEL_SIN_DERIVA
        if nombre in suyo and mio[nombre] != suyo[nombre]
    ]
    assert not distintas, (
        f"el codigo de estas piezas del tunel ya no coincide con {repo}: {distintas}. "
        "Si el cambio es deliberado, documentalo y sacalo de TUNEL_SIN_DERIVA."
    )

    # El SSLRequest del protocolo vive en una constante: si deriva, el health check de
    # I5 deja de verificar lo que dice verificar y nadie se entera.
    _comparar_constantes(
        "tunnel.py", TUNEL_CONSTANTES, PAQUETE / "tunnel.py", ruta, paquete, repo
    )


def test_no_se_colo_el_reuso_por_destino():
    """
    DE-4 dejo I3 e I7 fuera de esta libreria. Si alguien los agrega sin revisar la
    decision, este test lo dice: no es que esten prohibidos para siempre, es que
    aparecer sin abrir DE-4 de nuevo seria un accidente.
    """
    from redshift_extractor import tunnel as tunnel_mod

    for nombre in ("tunnel_status", "close_all_tunnels", "close_tunnel", "ensure_tunnel"):
        assert not hasattr(tunnel_mod, nombre), (
            f"aparecio {nombre}: DE-4 dejo I3 e I7 fuera de esta libreria. "
            "Si ahora hacen falta, actualiza la decision en ESTANDAR.md primero."
        )


# -----------------------------------------------------------------------------
# Dobles de test
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("doble", ["sshserver.py", "fakepg.py"])
def test_los_dobles_de_test_son_identicos_a_los_de_la_referencia(doble):
    """
    No importan el paquete, asi que lo unico que se normaliza es el fin de linea.

    Se copiaron identicos justamente para que este test los pudiera cubrir: el
    docstring de `fakepg.py` menciona `probe_postgres`, que aqui se llama
    `probe_redshift`, y se dejo intacto a proposito.
    """
    repo, _paquete = REFERENCIA
    ajeno = FUNCIONES_DIR / repo / "tests" / doble
    if not ajeno.exists():
        pytest.skip(f"{repo} no tiene tests/{doble} en esta maquina.")

    mio = _sin_eol((Path(__file__).parent / doble).read_text(encoding="utf-8"))
    suyo = _sin_eol(ajeno.read_text(encoding="utf-8"))
    if mio != suyo:
        diff = "\n".join(
            list(
                difflib.unified_diff(
                    mio.splitlines(), suyo.splitlines(), "local", repo, lineterm="", n=1
                )
            )[:40]
        )
        pytest.fail(
            f"tests/{doble} divergio del de {repo}. Si el cambio es necesario, hazlo en "
            f"los dos repos o documenta por que dejan de ser copias.\n{diff}"
        )
