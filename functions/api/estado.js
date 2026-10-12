/**
 * Pages Function: /api/estado
 *
 * Reenvía la petición al Worker para que el navegador solo hable con
 * emt-44.pages.dev. Así no hay peticiones a terceros, que es lo que
 * bloquean los navegadores con protección de rastreo activada.
 */

const ORIGEN = "https://emt-44.esperance.workers.dev/api/estado";

export async function onRequest() {
  try {
    const r = await fetch(ORIGEN, { cf: { cacheTtl: 0 } });
    const cuerpo = await r.text();
    return new Response(cuerpo, {
      status: r.status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  } catch (e) {
    return new Response(
      JSON.stringify({ ts: Date.now(), error: "proxy: " + String(e.message || e) }),
      { status: 502, headers: { "content-type": "application/json; charset=utf-8" } }
    );
  }
}
