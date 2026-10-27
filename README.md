# Radar de producto Livezy

Cada día a las 08:00 (hora de Arabia Saudí) lee el catálogo, los precios, el stock y el orden de «más vendidos» de 43 tiendas
de suplementos en Shopify (`tiendas.json`). Los lunes y jueves también baja Google Trends de Arabia Saudí, Emiratos y mundial
(`palabras.json`). Todo se va guardando en `radar.db`, así que el histórico crece solo.

- Informe para leer: https://marcmestre1999-art.github.io/livezy-radar/
- El mismo informe en texto (el que le das a Claude): https://marcmestre1999-art.github.io/livezy-radar/informe.md

Para añadir una tienda o una búsqueda basta con editar `tiendas.json` o `palabras.json`. La tienda tiene que ser Shopify:
se comprueba abriendo `https://dominio/products.json`.

Comandos: `python radar.py tiendas | tendencias | informe | todo`.
