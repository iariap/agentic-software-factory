---
name: mi-mesa-recipe-image-prompt
description: Plantilla canónica de prompt para generar la fotografía de una receta de Mi Mesa (estilo editorial rioplatense, cenital 1:1, piedra crema). Usar cada vez que se genere una imagen para una receta del catálogo.
---

# Prompt de imagen de receta — Mi Mesa

Usá esta plantilla **cada vez** que se genere una imagen para una receta del
catálogo. Reemplazá los campos `{...}` con los datos de la receta concreta.

```text
Use case: photorealistic-natural

OBJETIVO
Generá una fotografía gastronómica editorial para la ficha de una receta de la aplicación "Mi mesa".

RECETA
Nombre: {NOMBRE_DE_LA_RECETA}
Descripción visual del plato terminado: {DESCRIPCIÓN_VISUAL_CONCRETA}
Ingredientes visibles permitidos: {INGREDIENTES_DECLARADOS}
Método de cocción: {HORNO / PLANCHA / PARRILLA / HERVIDO / SALTEADO / CRUDO}
Forma de presentación: {BOWL / PLATO LLANO / PLATO HONDO / FUENTE / VASO}
Cantidad representada: una única porción terminada.

SUJETO PRINCIPAL
Mostrar claramente {ELEMENTO_PRINCIPAL} como protagonista del plato.

Debe resultar reconocible por:
- {FORMA, CORTE O ESTRUCTURA}
- {COLOR CORRECTO DESPUÉS DE LA COCCIÓN}
- {TEXTURA ESPERADA}
- {DORADO, GRATINADO O TERMINACIÓN}
- {DISPOSICIÓN EN EL PLATO}

La guarnición debe ser {GUARNICIÓN}, preparada exclusivamente con los ingredientes declarados.

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
{INGREDIENTES_DECLARADOS}

No inventar guarniciones, salsas, toppings ni decoraciones.
No agregar ingredientes solamente para mejorar el aspecto.
Las cantidades y proporciones deben parecer compatibles con una porción real.
Respetar el método de cocción y la forma final indicados en la receta.

RESTRICCIONES ALIMENTARIAS
Restricciones de esta receta:
{ALERGIAS_Y_EXCLUSIONES}

No mostrar huevo, soja, tofu, tempeh, edamame ni derivados de soja.
No agregar gluten, lácteos, frutos secos, semillas u otros alérgenos si no están expresamente declarados.
Una hierba, semilla o salsa sólo puede aparecer cuando figure en los ingredientes.
Ante cualquier duda, omitir el ingrediente.

PRECISIÓN DEL PLATO
No sustituir visualmente el ingrediente principal por otro parecido.

Ejemplos:
- El salmón debe tener carne coral anaranjada, vetas claras y, si corresponde, piel visible. No debe parecer atún ni carne roja.
- Una hamburguesa de lentejas debe parecer un medallón vegetal compacto. No debe parecer carne.
- Un pudding de chía debe mostrar semillas hidratadas y textura espesa. No debe parecer sopa.
- Una crema o dip debe tener volumen y textura densa. No debe parecer una bebida.
- Una milanesa debe mostrar un empanado dorado uniforme y el acompañamiento declarado.
- Una pasta debe respetar exactamente el tipo de pasta indicado.
- Una preparación rellena debe mostrar claramente su recipiente y el relleno correspondiente.

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
Resolución de generación recomendada: 1024 × 1024 píxeles o superior.
Archivo final requerido: WebP de 1000 × 1000 píxeles.
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
Una fotografía cuadrada, cenital, cálida y fotorrealista de {NOMBRE_DE_LA_RECETA}, visualmente coherente con sus ingredientes y restricciones, con el plato completo centrado sobre piedra crema y lista para incorporarse al catálogo de "Mi mesa".
```

En `{DESCRIPCIÓN_VISUAL_CONCRETA}` debe ir algo concreto, por ejemplo:
> Un filete entero de salmón a la plancha, cocido y reconocible por su carne coral opaca, sus vetas claras y la piel dorada visible. Servido junto a una ensalada de garbanzos, tomate, pepino y hojas de espinaca, aliñada con limón, oliva y orégano.

## Reglas de uso

- Reemplazar SIEMPRE todos los `{...}` con datos reales de la receta.
- Respetar restricciones alimentarias: si la receta no declara un alérgeno,
  el prompt no debe pedirlo visualmente. Ante duda, omitir.
- Los ingredientes visibles deben coincidir con `amounts`/`ingredient_ids`
  de la receta; no inventar guarnición ni salsa.
- El estilo es consistente con todo el catálogo de Mi Mesa: cenital, 1:1,
  piedra crema, luz de ventana cálida.