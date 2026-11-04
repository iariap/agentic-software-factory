#!/usr/bin/env python3
"""Consulta métricas agregadas de Cloud Run de mi-mesa (Cloud Monitoring).

Solo agregados y percentiles; nunca payloads de logs. Usado por metrics.sh.

Entrada (env): GCP_PROJECT, HOURS.
Salida: JSON con request count, éxito %, p50/p95/p99 de latencia y conteo de
logs por severidad. Cualquier error de acceso se reporta como PENDIENTE.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

PROJECT = os.environ.get("GCP_PROJECT", "")
HOURS = int(os.environ.get("HOURS", "24"))

START = (datetime.now(UTC) - timedelta(hours=HOURS)).isoformat()


def gcloud(args: list[str]) -> str:
    """Corre gcloud y devuelve stdout; falla ruidoso si el comando falla."""
    result = subprocess.run(
        ["gcloud", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return result.stdout


def percentile(values: list[float], pct: float) -> float:
    """Percentil simple sobre valores ya expandidos por bucket."""
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, round(pct / 100 * (len(values) - 1)))
    return values[idx]


def expand_distribution(buckets: list, bounds: list[float]) -> list[float]:
    """Expande buckets de una distribución (Cloud Monitoring) en valores.

    `buckets` es la lista `bucketCounts` (enteros) o de dicts con `count`.
    """
    values: list[float] = []
    for i, b in enumerate(buckets):
        count = int(b.get("count", 0)) if isinstance(b, dict) else int(b)
        if count <= 0:
            continue
        if i < len(bounds):
            mid = float(bounds[i])
        elif i == len(bounds) and bounds:
            mid = float(bounds[-1]) * 1.5
        else:
            mid = 1.0
        values.extend([mid] * count)
    return values


def main() -> int:
    if not PROJECT:
        print(json.dumps({"status": "PENDING", "reason": "GCP_PROJECT no definido"}))
        return 1

    result: dict[str, Any] = {"project": PROJECT, "hours": HOURS}
    try:
        # request_count: total y por clase de respuesta (2xx/5xx).
        count_out = gcloud(
            [
                "monitoring",
                "time-series",
                "list",
                "--project",
                PROJECT,
                "--filter",
                'metric.type="run.googleapis.com/request_count" '
                'AND resource.type="cloud_run_revision" '
                f'AND metric.label.response_code_class!="200" AND timestamp>="{START}"',
                "--aggregation-alignment-period=3600s",
                "--format=json",
            ]
        )
        series = json.loads(count_out)
        total = 0
        by_class: dict[str, int] = {}
        for s in series:
            cls = s.get("metric", {}).get("labels", {}).get("response_code_class", "?")
            for p in s.get("points", []):
                v = int(p.get("value", {}).get("int64Value", 0))
                total += v
                by_class[cls] = by_class.get(cls, 0) + v
        result["requests"] = total
        result["by_response_class"] = by_class
        ok = by_class.get("2xx", 0) + by_class.get("3xx", 0) + by_class.get("200", 0)
        result["success_rate_pct"] = round(ok / total * 100, 2) if total else None

        # request_latencies: percentiles.
        lat_out = gcloud(
            [
                "monitoring",
                "time-series",
                "list",
                "--project",
                PROJECT,
                "--filter",
                'metric.type="run.googleapis.com/request_latencies" '
                'AND resource.type="cloud_run_revision" '
                f'AND timestamp>="{START}"',
                "--aggregation-alignment-period=3600s",
                "--format=json",
            ]
        )
        lat_series = json.loads(lat_out)
        values: list[float] = []
        for s in lat_series:
            for p in s.get("points", []):
                dist = p.get("value", {}).get("distributionValue", {})
                buckets = dist.get("bucketCounts", [])
                bounds = dist.get("explicitBounds", {}).get("bounds", [])
                values.extend(expand_distribution(buckets, bounds))
        if values:
            result["latency"] = {
                "p50_ms": round(percentile(values, 50) * 1000, 1),
                "p95_ms": round(percentile(values, 95) * 1000, 1),
                "p99_ms": round(percentile(values, 99) * 1000, 1),
                "n": len(values),
            }
        else:
            result["latency"] = None

        # Logs por severidad (sin payloads).
        log_out = gcloud(
            [
                "logging",
                "read",
                f'resource.type=cloud_run_revision AND timestamp>="{START}"',
                "--project",
                PROJECT,
                "--limit",
                "5000",
                "--format=csv(severity)",
            ]
        )
        sev: dict[str, int] = {}
        for line in log_out.splitlines():
            line = line.strip()
            if line:
                sev[line] = sev.get(line, 0) + 1
        result["logs_by_severity"] = sev
        result["status"] = "OK"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except SystemExit:
        print(json.dumps({"status": "PENDING", "reason": "acceso a Cloud Monitoring/Logging falló"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
