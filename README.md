# EMT 44

Panel para la línea 44 de la EMT de Madrid: ida desde Rubio y Galí (parada 1670) y vuelta desde Marqués de Viana (salidas de la parada 3895 y llegadas de la 2402).

## Piezas

| Ruta | Qué es | Dónde se publica |
|---|---|---|
| `worker.js` + `wrangler.jsonc` | Worker que consulta la API de EMT y devuelve `/api/estado` | `emt-44.esperance.workers.dev` |
| `publico/index.html` | Panel que se ve en el móvil | Cloudflare Pages |
| `functions/api/estado.js` | Pages Function que reenvía `/api/estado` al Worker (el navegador del móvil bloqueaba la llamada directa a `workers.dev`) | Cloudflare Pages |
| `emt_44.py` | Script de apoyo (`resolver`, `sondeo`) | Solo en local |
| `datos/` | Respuestas y sondeos de muestra | Solo en el repo |

`functions/` va en la raíz, al lado de `publico/`, no dentro.

## Secretos

Las credenciales del portal de EMT **no están en el repo**. Están como secretos del Worker en Cloudflare: `EMT_EMAIL` y `EMT_PASSWORD`.

## Despliegue

Con la integración Git, cada push a `main` publica solo el Worker (`emt-44`) y el panel (`emt-44-git.pages.dev`). El panel antiguo `emt-44.pages.dev` queda de reserva y ya no se actualiza.

**No desplegar desde la copia local `~/emt44`**: el código vivo es el de este repo. Para Pages: sin comando de compilación, directorio de salida `publico`.

Despliegue manual de reserva, desde la carpeta del proyecto:

```sh
npx wrangler deploy                                         # Worker
npx wrangler pages deploy publico --project-name emt-44-git # Panel
```
