#!/usr/bin/env bash
# Espera hasta-condición con techo (patrón estándar del harness: nunca sleep a ciegas).
#
# Uso:
#   scripts/wait_until.sh <techo_seg> <intervalo> -- <cmd> [args...]
#   scripts/wait_until.sh <techo_seg> <intervalo> -c "<cmd shell (permite pipes/redirects)>"
#
# Corre <cmd> cada <intervalo> hasta que salga 0, o se agote el techo.
# - Salida limpia para piping: SOLO la salida del intento exitoso va a stdout
#   (los intentos fallidos y el progreso van a stderr).
# - El stderr del comando pasa directo (debug en vivo).
# - Exit: 0 = condición cumplida; 1 = timeout; 2 = uso.
set -euo pipefail

MAX="${1:?uso: $0 <techo_seg> <intervalo> -- <cmd...> | $0 <techo_seg> <intervalo> -c \"<cmd>\"}"
IV="${2:?falta <intervalo>}"
shift 2

for v in "$MAX" "$IV"; do
  case "$v" in ''|*[!0-9]*) echo "techo/intervalo deben ser enteros: '$v'" >&2; exit 2 ;; esac
done
[ "$IV" -ge 1 ] || { echo "intervalo >= 1" >&2; exit 2; }

[ "$#" -ge 1 ] || { echo "falta el comando" >&2; exit 2; }
mode=args
case "$1" in
  --) shift; [ "$#" -ge 1 ] || { echo "-- requiere <cmd...>" >&2; exit 2; } ;;
  -c) shift; [ "$#" -eq 1 ] || { echo "-c requiere UN string shell" >&2; exit 2; }; mode=shell ;;
  *) echo "uso: $0 <techo> <intervalo> -- <cmd...> | -c \"<cmd>\"" >&2; exit 2 ;;
esac

START=$SECONDS
while :; do
  if [ "$mode" = shell ]; then
    if out="$(bash -c "$1")"; then
      printf '%s\n' "$out"
      echo "wait_until: ok tras $((SECONDS - START))s" >&2
      exit 0
    fi
    last="$out"
  else
    if out="$("$@")"; then
      printf '%s\n' "$out"
      echo "wait_until: ok tras $((SECONDS - START))s" >&2
      exit 0
    fi
    last="$out"
  fi
  printf 'wait_until [%ss]: condición no cumplida aún\n' "$((SECONDS - START))" >&2
  if [ -n "${last:-}" ]; then
    printf '%s\n' "$last" | sed 's/^/  | /' >&2
  fi
  remaining=$((MAX - (SECONDS - START)))
  if [ "$remaining" -le 0 ]; then
    echo "wait_until: TIMEOUT (${MAX}s) sin cumplir la condición" >&2
    exit 1
  fi
  if [ "$IV" -lt "$remaining" ]; then sleep_s=$IV; else sleep_s=$remaining; fi
  sleep "$sleep_s"
done
