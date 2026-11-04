#!/usr/bin/env python3
"""Genera los prompts de imagen para el lote 2 (20 recetas) desde el catálogo,
siguiendo la plantilla canónica del skill mi-mesa-recipe-image-prompt.

Salida: state/reports/celiac-l2/imagenes-prompts.md con un bloque ```text por
receta, en el mismo orden que scripts/generate_images.py espera.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import app_config as _ac  # noqa: E402

APP = Path(_ac.load_registry("mi-mesa")["checkout_abs"])
OUT = ROOT / "state" / "reports" / "celiac-l2"

# Recetas del lote 2 en orden de generación.
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

# Plantilla base (versión canónica; se completa por receta).
TEMPLATE = """Use case: photorealistic-natural

OBJETIVO
Generá una fotografía gastronómica editorial para la ficha de una receta de la aplicación "Mi mesa".

RECETA
Nombre: {NOMBRE}
Descripción visual del plato terminado: {DESC}
Ingredientes visibles permitidos: {INGREDIENTES}
Método de cocción: {METODO}
Forma de presentación: {PRESENTACION}
Cantidad representada: una única porción terminada.

SUJETO PRINCIPAL
Mostrar claramente {SUJETO} como protagonista del plato.

Debe resultar reconocible por:
- {FORMA}
- {COLOR}
- {TEXTURA}
- {DORADO}
- {DISPOSICION}

La guarnición debe ser {GUARNICION}, preparada exclusivamente con los ingredientes declarados.

ESTILO VISUAL
Fotografía gastronómica editorial fotorrealista.
Presentación casera cuidada, moderna y apetecible.
Texturas naturales y creíbles.
Nada excesivamente perfecto, plástico, brillante o publicitario.
El plato debe parecer recién preparado, con humedad y dorado realistas.
Mantener el estilo visual consistente con todo el catálogo de "Mi mesa".

COMPOSICIÓN
Vista cenital exacta a 90 grados.
Encuadre estrictamente cuadrado, relación 1:1.
Un único plato, bowl o recipiente.
Recipiente completo y perfectamente visible.
Sujeto centrado.
Margen uniforme y generoso alrededor del plato.
No cortar el recipiente ni ningún componente principal.
La comida debe ocupar aproximadamente entre el 65 % y el 75 % del cuadro.
Debe funcionar correctamente tanto como miniatura de tarjeta usando `object-fit: cover` como en el encabezado de detalle.
No colocar elementos importantes cerca de los bordes.

SUPERFICIE Y ESCENA
Mesa de piedra natural color crema cálido.
Textura mineral suave y discreta.
Fondo limpio y continuo.
Sin manteles.
Sin tablas.
Sin otros platos.
Sin objetos decorativos.
Sin ingredientes dispersos alrededor del recipiente.
Sin una escena de cocina completa.

ILUMINACIÓN
Luz natural de ventana, suave y cálida.
Dirección lateral sutil.
Sombras delicadas y difusas.
Contraste moderado.
Altas luces controladas.
Sin reflejos artificiales intensos.
Sin iluminación dramática.
Sin flash.
Balance de blancos cálido y natural.

PALETA DE COLORES
Base:
- crema cálido
- marfil
- beige piedra
- cerámica natural

Acentos:
- verde oliva suave
- terracota
- rojo tomate
- naranja calabaza
- tonos dorados de cocción
- colores naturales de los ingredientes

Evitar:
- saturación excesiva
- colores neón
- dominantes azules
- blancos clínicos
- negros muy profundos
- filtros anaranjados artificiales

VAJILLA
Cerámica artesanal mate en tonos crema, marfil o beige claro.
Diseño simple.
Borde fino y discreto.
Sin dibujos.
Sin texto.
Sin logotipos.
Sin vajilla excesivamente brillante.

FIDELIDAD DE LA RECETA
Mostrar únicamente ingredientes presentes en:
{INGREDIENTES}

No inventar guarniciones, salsas, toppings ni decoraciones.
No agregar ingredientes solamente para mejorar el aspecto.
Las cantidades y proporciones deben parecer compatibles con una porción real.
Respetar el método de cocción y la forma final indicados en la receta.

RESTRICCIONES ALIMENTARIAS
Restricciones de esta receta:
apta celíacos; {PATRON}

No mostrar soja, tofu, tempeh, edamame ni derivados de soja si no están declarados.
No agregar gluten, lácteos, frutos secos u otros alérgenos si no están expresamente declarados.
Una hierba, semilla o salsa sólo puede aparecer cuando figure en los ingredientes.
Ante cualquier duda, omitir el ingrediente.

PRECISIÓN DEL PLATO
No sustituir visualmente el ingrediente principal por otro parecido.

Ejemplos:
- {PRECISION}

CALIDAD TÉCNICA
Imagen nítida y detallada.
Profundidad de campo amplia: todo el plato debe verse enfocado.
Texturas realistas.
Sin deformaciones.
Sin ingredientes fusionados.
Sin duplicaciones imposibles.
Sin bordes artificiales.
Sin desenfoque de movimiento.
Sin ruido digital.
Sin apariencia de ilustración, render 3D o pintura.

SALIDA
Generar en formato cuadrado 1:1.
Resolución de generación recomendada: 1024x1024 píxeles o superior.
Archivo final requerido: WebP de 1000x1000 píxeles.
Calidad WebP recomendada: 84.
Perfil de color: sRGB.
Sin transparencia.

RESTRICCIONES FINALES
No texto.
No letras.
No etiquetas.
No logotipos.
No marcas de agua.
No envases.
No packaging.
No manos.
No personas.
No cubiertos sostenidos.
No utensilios que oculten el plato.
No servilletas decorativas.
No flores.
No elementos flotantes.
No segundo plato.
No collage.
No múltiples ángulos.
No vista oblicua.
No recortar el plato.
No representar una receta diferente.

RESULTADO ESPERADO
Una fotografía cuadrada, cenital, cálida y fotorrealista de {NOMBRE}, visualmente coherente con sus ingredientes y restricciones, con el plato completo centrado sobre piedra crema y lista para incorporarse al catálogo de "Mi mesa"."""

# Datos específicos por receta.
SPECS = {
    "empanadas-pollo-masa-arroz": {
        "NOMBRE": "Empanadas de pollo con masa de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Dos empanadas de pollo doradas con masa de arroz, bien cerradas con repulgue visible, sobre un plato llano.",
        "SUJETO": "las empanadas de pollo con masa de arroz",
        "FORMA": "forma de media luna con repulgue, masa dorada",
        "COLOR": "dorado uniforme de la masa horneada",
        "TEXTURA": "masa crocante por fuera, relleno húmedo de pollo y cebolla",
        "DORADO": "dorado de horno en toda la superficie",
        "DISPOSICION": "dos empanadas centradas, una sobre otra levemente",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La empanada debe ser de masa de arroz con repulgue, no un pan ni una empanada de hojaldre de trigo. El relleno es de pollo desmenuzado con cebolla, no de carne roja.",
    },
    "empanadas-carne-masa-arroz": {
        "NOMBRE": "Empanadas de carne con masa de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Dos empanadas de carne doradas con masa de arroz y repulgue, sobre un plato llano.",
        "SUJETO": "las empanadas de carne con masa de arroz",
        "FORMA": "media luna con repulgue, masa dorada",
        "COLOR": "dorado uniforme del horneado",
        "TEXTURA": "masa crocante, relleno de carne picada jugoso",
        "DORADO": "dorado de horno",
        "DISPOSICION": "dos empanadas centradas",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La empanada debe tener masa de arroz con repulgue y relleno de carne vacuna picada con zanahoria, no un pan ni hojaldre.",
    },
    "empanadas-verdura-masa-arroz": {
        "NOMBRE": "Empanadas de verdura con masa de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Dos empanadas de verdura y ricota doradas con masa de arroz, repulgue visible, sobre un plato llano.",
        "SUJETO": "las empanadas de verdura con masa de arroz",
        "FORMA": "media luna con repulgue",
        "COLOR": "dorado con verduras verdes asomando",
        "TEXTURA": "masa crocante, relleno de acelga y ricota cremoso",
        "DORADO": "dorado de horno",
        "DISPOSICION": "dos empanadas centradas",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "La empanada debe ser de masa de arroz con relleno de acelga y ricota, no de carne ni con masa de trigo.",
    },
    "tarta-pollo-masa-arroz": {
        "NOMBRE": "Tarta de pollo con masa de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una porción triangular de tarta de pollo con masa de arroz dorada, corte visible con relleno de pollo, cebolla y ricota, sobre un plato llano.",
        "SUJETO": "la porción de tarta de pollo con masa de arroz",
        "FORMA": "porción triangular con masa y relleno visibles",
        "COLOR": "masa dorada, relleno claro de pollo y ricota",
        "TEXTURA": "masa tierna, relleno húmedo",
        "DORADO": "bordes dorados de horno",
        "DISPOSICION": "una porción centrada, corte hacia el espectador",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La tarta debe tener masa de arroz y relleno de pollo desmenuzado con ricota, no masa de trigo ni relleno de carne roja.",
    },
    "tarta-pollo-choclo": {
        "NOMBRE": "Tarta de pollo y choclo sin TACC",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una porción de tarta de pollo y choclo con masa de arroz dorada, corte visible con granos de choclo, sobre un plato llano.",
        "SUJETO": "la porción de tarta de pollo y choclo",
        "FORMA": "porción triangular, relleno con granos de choclo visibles",
        "COLOR": "masa dorada, choclo amarillo en el relleno",
        "TEXTURA": "masa tierna, relleno jugoso con granos",
        "DORADO": "bordes dorados",
        "DISPOSICION": "una porción centrada",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La tarta debe mostrar granos de choclo y pollo en el relleno, no una tarta de jamón y queso ni masa de trigo.",
    },
    "tarta-atun-masa-arroz": {
        "NOMBRE": "Tarta de atún con masa de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una porción de tarta de atún con masa de arroz dorada, corte visible con atún, tomate y ricota, sobre un plato llano.",
        "SUJETO": "la porción de tarta de atún",
        "FORMA": "porción triangular con relleno visible",
        "COLOR": "masa dorada, atún rosado con tomate",
        "TEXTURA": "masa tierna, relleno húmedo",
        "DORADO": "bordes dorados",
        "DISPOSICION": "una porción centrada",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La tarta debe mostrar atún desmenuzado en el relleno, no pollo ni carne.",
    },
    "milanesa-carne-maiz": {
        "NOMBRE": "Milanesa de carne rebozada en harina de maíz",
        "METODO": "SARTÉN",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una milanesa de carne rebozada en harina de maíz, dorada y crocante, sobre un plato llano.",
        "SUJETO": "la milanesa de carne rebozada en harina de maíz",
        "FORMA": "corte de carne aplanado con rebozado",
        "COLOR": "rebozado dorado uniforme",
        "TEXTURA": "costra crocante de harina de maíz",
        "DORADO": "dorado parejo de sartén",
        "DISPOSICION": "milanesa entera centrada",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "Una milanesa debe mostrar un empanado dorado uniforme; la de carne no debe parecer pescado ni pollo.",
    },
    "milanesa-pollo-chickpea": {
        "NOMBRE": "Milanesa de pollo rebozada en harina de garbanzo",
        "METODO": "SARTÉN",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una milanesa de pollo rebozada en harina de garbanzo, dorada y crocante, sobre un plato llano.",
        "SUJETO": "la milanesa de pollo rebozada en harina de garbanzo",
        "FORMA": "filete de pollo aplanado con rebozado",
        "COLOR": "rebozado dorado",
        "TEXTURA": "costra crocante",
        "DORADO": "dorado de sartén",
        "DISPOSICION": "milanesa entera centrada",
        "GUARNICION": "ninguna",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "Una milanesa debe mostrar un empanado dorado uniforme; la de pollo no debe parecer carne roja ni pescado.",
    },
    "milanesa-berenjena-maiz": {
        "NOMBRE": "Milanesa de berenjena rebozada en harina de maíz",
        "METODO": "SARTÉN",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Rodajas de milanesa de berenjena rebozadas en harina de maíz, doradas, sobre un plato llano.",
        "SUJETO": "las milanesas de berenjena",
        "FORMA": "rodajas de berenjena aplanadas con rebozado",
        "COLOR": "dorado con interior de berenjena",
        "TEXTURA": "crocante por fuera, berenjena tierna por dentro",
        "DORADO": "dorado de sartén",
        "DISPOSICION": "dos rodajas centradas",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "La milanesa de berenjena debe mostrar la berenjena tierna bajo el rebozado, no carne.",
    },
    "pizza-arroz-mozzarella": {
        "NOMBRE": "Pizza con masa de arroz y mozzarella",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una pizza redonda con masa de arroz, cubierta de tomate, mozzarella derretida y orégano, sobre un plato llano.",
        "SUJETO": "la pizza con masa de arroz",
        "FORMA": "pizza redonda de borde bajo",
        "COLOR": "masa dorada, queso fundido amarillo, salsa roja",
        "TEXTURA": "base firme, queso fundido",
        "DORADO": "bordes dorados, queso gratinado",
        "DISPOSICION": "pizza entera centrada, cortada en porciones",
        "GUARNICION": "salsa de tomate y mozzarella",
        "PATRON": "sin gluten/trigo/avena",
        "PRECISION": "La pizza debe tener masa de arroz firme, no masa de trigo inflada ni pan; la cobertura es tomate y mozzarella.",
    },
    "pizza-garbanzo-verduras": {
        "NOMBRE": "Pizza de garbanzo con verduras",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una pizza con base de harina de garbanzo, cubierta de tomate, zucchini y pimiento en rodajas con mozzarella, sobre un plato llano.",
        "SUJETO": "la pizza de garbanzo con verduras",
        "FORMA": "pizza redonda de base firme",
        "COLOR": "base amarillenta, verduras verdes y rojas, queso fundido",
        "TEXTURA": "base firme de garbanzo, verduras asadas",
        "DORADO": "bordes dorados",
        "DISPOSICION": "pizza entera centrada",
        "GUARNICION": "tomate, zucchini, pimiento y mozzarella",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "La base es de harina de garbanzo, de color amarillo claro y firme, no masa de trigo; muestra las rodajas de verduras.",
    },
    "canelones-espinaca-panqueque": {
        "NOMBRE": "Canelones de espinaca y ricota con panqueques de arroz",
        "METODO": "HORNO",
        "PRESENTACION": "FUENTE",
        "DESC": "Dos canelones de espinaca y ricota enrollados en panqueques de arroz, con salsa de tomate y mozzarella gratinada, en una fuente.",
        "SUJETO": "los canelones de espinaca y ricota",
        "FORMA": "cilindros de panqueque relleno con salsa encima",
        "COLOR": "salsa roja, queso gratinado dorado",
        "TEXTURA": "panqueque tierno, relleno cremoso",
        "DORADO": "gratinado dorado de mozzarella",
        "DISPOSICION": "dos canelones alineados en la fuente",
        "GUARNICION": "salsa de tomate y mozzarella",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "Los canelones deben ser panqueques de arroz rellenos de espinaca y ricota con salsa de tomate, no pasta de trigo.",
    },
    "guiso-porotos-calabaza": {
        "NOMBRE": "Guiso de porotos blancos y calabaza",
        "METODO": "OLLA",
        "PRESENTACION": "PLATO HONDO",
        "DESC": "Un guiso de porotos blancos y calabaza, espeso, con tomate y verduras, en un plato hondo.",
        "SUJETO": "el guiso de porotos blancos y calabaza",
        "FORMA": "guiso espeso con porotos y cubos de calabaza",
        "COLOR": "naranja de la calabaza, porotos blancos, rojo del tomate",
        "TEXTURA": "espeso y caldoso a la vez",
        "DORADO": "sin dorar, color natural de cocción",
        "DISPOSICION": "guiso centrado en el plato hondo",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "El guiso debe mostrar porotos blancos enteros y cubos de calabaza, no ser una sopa ni un puré.",
    },
    "guiso-lentejas-arroz": {
        "NOMBRE": "Guiso de lentejas con arroz y verduras",
        "METODO": "OLLA",
        "PRESENTACION": "PLATO HONDO",
        "DESC": "Un guiso de lentejas con arroz, cebolla, zanahoria y pimiento, espeso, en un plato hondo.",
        "SUJETO": "el guiso de lentejas con arroz",
        "FORMA": "guiso con lentejas y granos de arroz visibles",
        "COLOR": "lentejas marrones, arroz blanco, verduras",
        "TEXTURA": "espeso y caldoso",
        "DORADO": "sin dorar",
        "DISPOSICION": "guiso centrado",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "El guiso debe mostrar lentejas enteras y granos de arroz, no ser una crema ni un puré.",
    },
    "budin-manzana-canela": {
        "NOMBRE": "Budín de manzana y canela sin TACC",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una rebanada de budín de manzana y canela, de miga húmeda con trozos de manzana, sobre un plato llano.",
        "SUJETO": "la rebanada de budín de manzana y canela",
        "FORMA": "rebanada rectangular de budín con corte",
        "COLOR": "miga clara con trozos de manzana, corteza dorada",
        "TEXTURA": "miga húmeda y tierna",
        "DORADO": "corteza dorada",
        "DISPOSICION": "una rebanada centrada, corte al frente",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "El budín de manzana debe mostrar trozos de manzana en la miga, no ser un budín liso de vainilla.",
    },
    "torta-manzana-nuez": {
        "NOMBRE": "Torta de manzana y nuez sin TACC",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Una porción de torta de manzana y nuez, miga húmeda con cubos de manzana y nueces, sobre un plato llano.",
        "SUJETO": "la porción de torta de manzana y nuez",
        "FORMA": "porción triangular o cuadrada con corte",
        "COLOR": "miga dorada clara con manzana y nueces",
        "TEXTURA": "miga húmeda con cubos y nueces",
        "DORADO": "corteza dorada",
        "DISPOSICION": "una porción centrada",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "La torta de manzana debe mostrar cubos de manzana y trozos de nuez, no ser un budín liso.",
    },
    "palitos-maiz-especiados": {
        "NOMBRE": "Palitos de maíz especiados",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Un puñado de palitos de maíz especiados, dorados y crocantes, apilados en un plato llano o bowl.",
        "SUJETO": "los palitos de maíz especiados",
        "FORMA": "bastones rectos de 1 cm de ancho",
        "COLOR": "dorado uniforme",
        "TEXTURA": "crocantes",
        "DORADO": "dorado de horno",
        "DISPOSICION": "palitos agrupados en el centro",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "Los palitos deben ser bastones crocantes de maíz, no papas fritas ni pan.",
    },
    "budines-chocolate-mini": {
        "NOMBRE": "Budines de chocolate mini sin TACC",
        "METODO": "HORNO",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Dos budines de chocolate mini, de miga marrón húmeda y corteza dorada, sobre un plato llano.",
        "SUJETO": "los budines de chocolate mini",
        "FORMA": "budines pequeños individuales",
        "COLOR": "miga marrón oscuro, corteza dorada",
        "TEXTURA": "miga húmeda",
        "DORADO": "corteza dorada",
        "DISPOSICION": "dos budines centrados",
        "GUARNICION": "ninguna",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "Los budines de chocolate deben tener miga marrón de cacao, no ser de vainilla ni de banana.",
    },
    "porridge-quinoa-manzana": {
        "NOMBRE": "Porridge de quinoa con manzana y canela",
        "METODO": "OLLA",
        "PRESENTACION": "BOWL",
        "DESC": "Un bowl de porridge de quinoa cremoso con manzana rallada y canela, tibio.",
        "SUJETO": "el porridge de quinoa con manzana",
        "FORMA": "crema espesa de quinoa con trozos de manzana",
        "COLOR": "crema clara con manzana y canela",
        "TEXTURA": "cremosa y espesa",
        "DORADO": "sin dorar, color natural",
        "DISPOSICION": "porridge centrado en el bowl",
        "GUARNICION": "manzana rallada y canela",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "El porridge debe ser cremoso y espeso con granos de quinoa, no una sopa ni un batido.",
    },
    "tortitas-banana-arroz": {
        "NOMBRE": "Tortitas de banana y arroz",
        "METODO": "SARTÉN",
        "PRESENTACION": "PLATO LLANO",
        "DESC": "Un apilado de tres tortitas doradas de banana y harina de arroz, con rodajas de banana encima.",
        "SUJETO": "las tortitas de banana y arroz",
        "FORMA": "tortitas redondas apiladas",
        "COLOR": "dorado con banana",
        "TEXTURA": "esponjosas con trozos de banana",
        "DORADO": "dorado de sartén",
        "DISPOSICION": "apiladas y centradas con banana encima",
        "GUARNICION": "rodajas de banana",
        "PATRON": "vegetariana; sin gluten/trigo/avena",
        "PRECISION": "Las tortitas deben ser panqueques esponjosos dorados de banana, no crepes finos.",
    },
}


def ingredients_text(recipe: dict) -> str:
    return ", ".join(recipe["ingredients"])


def main() -> int:
    catalog = json.loads((APP / "backend/src/omavu/data/catalog.json").read_text())
    by_id = {r["id"]: r for r in catalog}
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for rid in RECIPE_IDS:
        r = by_id[rid]
        spec = SPECS[rid]
        pattern = spec["PATRON"]
        base_mode = "omnivore" if any(m == "omnivore" for m in r.get("modes", [])) else "vegetarian"
        full_pattern = f"{base_mode}; " + pattern
        text = TEMPLATE.format(
            NOMBRE=spec["NOMBRE"],
            DESC=spec["DESC"],
            INGREDIENTES=ingredients_text(r),
            METODO=spec["METODO"],
            PRESENTACION=spec["PRESENTACION"],
            SUJETO=spec["SUJETO"],
            FORMA=spec["FORMA"],
            COLOR=spec["COLOR"],
            TEXTURA=spec["TEXTURA"],
            DORADO=spec["DORADO"],
            DISPOSICION=spec["DISPOSICION"],
            GUARNICION=spec["GUARNICION"],
            PATRON=full_pattern,
            PRECISION=spec["PRECISION"],
        )
        lines.append(f"## {rid}\n\n```text\n{text}\n```")
    (OUT / "imagenes-prompts.md").write_text(
        "# Prompts de imagen — Lote 2 (20 recetas)\n\n" + "\n\n---\n\n".join(lines) + "\n"
    )
    print(f"Prompts escritos: {len(RECIPE_IDS)} en {OUT / 'imagenes-prompts.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
