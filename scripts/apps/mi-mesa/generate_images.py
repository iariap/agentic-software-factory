#!/usr/bin/env python3
"""Genera las imágenes de recetas del lote 1 con Vertex AI (gemini-2.5-flash-image).

Lee los prompts de state/reports/celiac-l1/imagenes-prompts.md (bloques ```text),
envía cada uno a la API de generación de imágenes de Vertex AI y guarda el PNG
resultante en work/mi-mesa-recetario/work/recipe-images/<id>.png.

Uso:
  GEMINI_TOKEN="$(gcloud auth print-access-token)" python3 scripts/generate_images.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import app_config as _ac  # noqa: E402

APP = Path(_ac.load_registry("mi-mesa")["checkout_abs"])
PROMPTS = ROOT / "state" / "reports" / "celiac-l2" / "imagenes-prompts.md"
STAGING = APP / "work" / "recipe-images"

PROJECT = os.environ.get("GEMINI_PROJECT", "cellular-hold-423514-f2")
REGION = os.environ.get("GEMINI_REGION", "us-central1")
MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# Mapeo sección -> recipe id (orden del archivo de prompts).
RECIPE_IDS = [
    "empanadas-pollo-masa-arroz",
    "empanadas-carne-masa-arroz",
    "empanadas-verdura-masa-arroz",
    "tarta-pollo-masa-arroz",
    "tarta-pollo-choclo",
    "tarta-atun-masa-arroz",
    "milanesa-carne-maiz",
    "milanesa-pollo-chickpea",
    "milanesa-berenjena-maiz",
    "pizza-arroz-mozzarella",
    "pizza-garbanzo-verduras",
    "canelones-espinaca-panqueque",
    "guiso-porotos-calabaza",
    "guiso-lentejas-arroz",
    "budin-manzana-canela",
    "torta-manzana-nuez",
    "palitos-maiz-especiados",
    "budines-chocolate-mini",
    "porridge-quinoa-manzana",
    "tortitas-banana-arroz",
]


def get_token() -> str:
    tok = os.environ.get("GEMINI_TOKEN")
    if tok:
        return tok
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def extract_prompts() -> list[str]:
    text = PROMPTS.read_text()
    blocks = re.findall(r"^```text\n(.*?)^```", text, re.M | re.S)
    return [b.strip() for b in blocks]


def generate_image(prompt: str, token: str) -> bytes:
    url = (
        f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
        f"/locations/{REGION}/publishers/google/models/{MODEL}:generateContent"
    )
    payload = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
    ).encode()
    last_err: Exception | None = None
    for attempt in range(1, 6):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
            for part in data["candidates"][0]["content"]["parts"]:
                if part.get("inlineData", {}).get("mimeType", "").startswith("image/"):
                    return base64.b64decode(part["inlineData"]["data"])
            raise RuntimeError("no image part in response")
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code == 429:
                wait = 15 * attempt
                print(f"    429: reintento en {wait}s (intento {attempt}/5)")
                import time

                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_err = exc
            import time

            time.sleep(5 * attempt)
            continue
    raise RuntimeError(f"falló tras reintentos: {last_err}")


def main() -> int:
    prompts = extract_prompts()
    if len(prompts) != len(RECIPE_IDS):
        print(f"esperaba {len(RECIPE_IDS)} prompts, encontré {len(prompts)}", file=sys.stderr)
        return 1
    STAGING.mkdir(parents=True, exist_ok=True)
    token = get_token()
    for rid, prompt in zip(RECIPE_IDS, prompts, strict=True):
        out = STAGING / f"{rid}.png"
        if out.exists():
            print(f"  skip (ya existe): {rid}")
            continue
        print(f"  generando {rid}...")
        png = generate_image(prompt, token)
        out.write_bytes(png)
        print(f"    ok -> {out} ({len(png)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
