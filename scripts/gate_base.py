#!/usr/bin/env python3
"""Frescura de la base de un PR: ¿el head corre los gates del main actual?

Clase de error que codifica (2026-10-23, x6200 #279): en `pull_request` GitHub usa
el workflow de la rama HEAD. Una rama sin rebasar corre los gates VIEJOS, así que
un verde no dice nada del `main` actual. #279 se mergeó con una rama anterior a
que existiera el gate de contraste (#277): el gate nunca corrió, el drift del
baseline quedó invisible y `main` quedó rojo en silencio hasta #290.

El criterio es el que decide qué gates corren: los archivos de workflow. Si la
base actual cambió `.github/workflows/**` (o `.github/actions/**`) desde el
merge-base del PR, el head ejecuta una definición de gates anterior ⇒ `stale`.

Lo que NO se usa:
  - el estado del PR (`mergeStateStatus`, "behind by N commits"): no dice si los
    gates corrieron, sólo cómo está el merge;
  - `git merge-base --is-ancestor origin/main <head>` contra el tip de main: con
    squash-merge la ascendencia da falsos negativos. Se compara el head contra el
    merge-base REAL con la base que reporta la API (`baseRefOid`), y el veredicto
    sale del diff de los workflows entre ese merge-base y la base.

Uso:
  scripts/gate_base.py <app> <base-sha> <head-sha> [--checkout PATH] [--trunk RAMA]

Exit: 0 = fresh (el head corre los gates de la base actual) | 1 = stale (corre
gates viejos: hay que rebasar) | 2 = unknown (no se puede probar: sin checkout,
sin el commit, sin merge-base). Un gate que no se puede probar NO es un gate
verde, pero la política la decide cada caller (asf#65): `merge.sh` BLOQUEA sólo
`stale` y AVISA en `unknown` (no se puede verificar no compra seguridad y agrega
una forma nueva de quedarse trabado); `auto-merge.py` es fail-closed en los dos.
stdout: una línea `<status>: <motivo>` (el resto de la evidencia va ahí mismo).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import app_config  # noqa: E402

# Lo que DEFINE qué gates corre el CI. Un cambio acá en la base y no en el head
# significa que el head ejecuta otra definición de gates.
GATE_PATHS: tuple[str, ...] = (".github/workflows", ".github/actions")

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    """Resultado de la comparación base↔head, con el motivo legible."""

    status: str
    detail: str
    workflows: tuple[str, ...] = ()


def classify(base: str, head: str, merge_base: str, changed: tuple[str, ...]) -> Verdict:
    """Decisión pura (sin git): dado el diff, ¿el head corre los gates de la base?

    `changed` son los paths que difieren entre `merge_base` y `base` restringidos
    a GATE_PATHS; se filtra igual acá para que un caller no pueda colar un diff
    completo y declarar stale por cualquier cambio.
    """
    if not base or not head:
        return Verdict(UNKNOWN, "la API no devolvió baseRefOid/headRefOid del PR")
    if base == head:
        return Verdict(FRESH, f"head == base ({head[:12]})")
    if not merge_base:
        return Verdict(UNKNOWN, f"sin merge-base entre {base[:12]} y {head[:12]}")
    if merge_base == base:
        return Verdict(FRESH, f"el head contiene la base actual ({base[:12]})")
    gates = tuple(sorted(p for p in changed if p.startswith(GATE_PATHS)))
    if not gates:
        return Verdict(
            FRESH,
            f"el head está detrás de la base ({base[:12]}) pero los workflows son idénticos",
        )
    return Verdict(
        STALE,
        f"la base {base[:12]} cambió los gates desde el merge-base {merge_base[:12]}"
        f" ⇒ el head corre gates viejos; acción: rebasar sobre origin/main y esperar"
        f" el run de la rama rebasada",
        gates,
    )


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
    )


def _has_commit(checkout: Path, sha: str) -> bool:
    return _git(checkout, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def base_freshness(checkout: Path | None, base: str, head: str, trunk: str | None = None) -> Verdict:
    """Verdict para (base, head) leyendo el checkout local.

    `trunk` habilita un `git fetch origin <trunk>` best-effort cuando falta el
    commit de la base (el harness siempre tiene red: ya usa `gh`). Si después del
    fetch el objeto sigue faltando, el veredicto es `unknown`, nunca `fresh`.
    """
    if checkout is None or not checkout.is_dir():
        return Verdict(
            UNKNOWN,
            "sin checkout de la app: no se puede leer el árbol del PR"
            " (scripts/sync-work.sh <app>, o pasá --checkout)",
        )
    if not base or not head:
        return Verdict(UNKNOWN, "la API no devolvió baseRefOid/headRefOid del PR")
    if base == head:
        return Verdict(FRESH, f"head == base ({head[:12]})")
    if trunk and (not _has_commit(checkout, base) or not _has_commit(checkout, head)):
        _git(checkout, "fetch", "--quiet", "origin", trunk)
    for sha, rol in ((base, "base"), (head, "head")):
        if not _has_commit(checkout, sha):
            accion = f"git fetch origin {trunk}" if rol == "base" and trunk else "traé la rama del PR"
            return Verdict(
                UNKNOWN,
                f"el commit {rol} {sha[:12]} no está en el checkout ({checkout}): {accion}",
            )
    merge_base_out = _git(checkout, "merge-base", base, head)
    merge_base = merge_base_out.stdout.strip() if merge_base_out.returncode == 0 else ""
    if not merge_base:
        return Verdict(UNKNOWN, f"sin merge-base entre {base[:12]} y {head[:12]}")
    diff = _git(checkout, "diff", "--name-only", merge_base, base, "--", *GATE_PATHS)
    if diff.returncode != 0:
        return Verdict(UNKNOWN, f"git diff falló: {diff.stderr.strip()}")
    changed = tuple(diff.stdout.split())
    return classify(base, head, merge_base, changed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app", help="id de app (config/apps/<id>.json)")
    ap.add_argument("base", help="baseRefOid del PR (la base ACTUAL, la reporta la API)")
    ap.add_argument("head", help="headRefOid del PR")
    ap.add_argument("--checkout", help="checkout a leer (default: el del registry)")
    ap.add_argument("--trunk", help="rama trunk para el fetch best-effort (default: la del registry)")
    a = ap.parse_args()

    reg = app_config.load_registry(a.app)
    checkout = Path(a.checkout) if a.checkout else Path(reg["checkout_abs"])
    trunk = a.trunk or str(reg.get("branch", "main"))
    verdict = base_freshness(checkout, a.base, a.head, trunk)
    detail = f"{verdict.status}: {verdict.detail}"
    if verdict.workflows:
        detail += f" [{', '.join(verdict.workflows)}]"
    print(detail)
    return {FRESH: 0, STALE: 1}.get(verdict.status, 2)


if __name__ == "__main__":
    raise SystemExit(main())
