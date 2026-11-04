#!/usr/bin/env bash
# Helpers comunes de las matrices de checks (scripts/apps/*/check-matrix.sh).
# Se sourcea desde cada matriz; no se ejecuta solo.

# has() <ERE>: verdadero si $CHANGED (lista de paths del diff) matchea el patrón.
#
# El patrón NO va por un pipe con `grep -q`: grep sale en el primer match, el
# productor muere con SIGPIPE (141) y con `set -o pipefail` ese 141 gana la
# tubería: has() devuelve falso aunque haya match. El bloque se saltea sin avisar
# y la matriz termina en verde sin haber corrido el chequeo. El here-string no
# tiene productor en un pipe, así que no hay SIGPIPE posible.
has() { grep -qE "$1" <<<"$CHANGED"; }

# skip() <bloque> <razón...>: deja explícito que un bloque NO corrió.
# "No corrió" nunca tiene que parecer "pasó": la línea [SKIP] queda en la salida
# con el motivo, y la matriz no puede reportar un verde silencioso por omisión.
skip() {
  local bloque="$1"; shift
  echo "==> [SKIP] $bloque: $*"
}
