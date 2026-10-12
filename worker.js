/**
 * Worker proxy para el panel de la linea 44 (EMT Madrid).
 *
 * Rutas:
 *   GET /api/estado   ->  { ts, ida: {...}, vuelta: {...} }
 *
 * Bindings necesarios:
 *   KV        (KV namespace)  cache del accessToken
 *   DB        (D1, opcional)  registro para medir precision
 * Secretos:
 *   EMT_EMAIL, EMT_PASSWORD   o   EMT_CLIENT_ID, EMT_PASSKEY
 *
 * Toda la logica sucia vive aqui: filtrado de linea, centinelas, orden,
 * estabilidad de la hora absoluta y respaldo con horario teorico.
 * El panel solo pinta.
 */

const BASE = "https://openapi.emtmadrid.es";
const LINEA = "44";
const PARADA_IDA = "1670";     // Rubio y Gali - Jeronima Llorente, hacia Marques de Viana
const PARADA_VUELTA = "3895";  // Marques de Viana, cabecera: SALIDAS hacia Callao
const PARADA_LLEGADA = "2402"; // Ofelia Nieto-Paseo de la Direccion: LLEGADAS a cabecera

// estimateArrive por encima de esto no es un tiempo, es un centinela.
const CENTINELA = 3600;
// Horizonte a partir del cual el panel dice "+20 min" en vez de un numero.
const HORIZONTE = 20 * 60;

// Salidas teoricas de cabecera (3895), extraidas del GTFS de EMT.
// Minutos desde medianoche. Se regeneran a mano cuando EMT cambia horarios.
// Sustituir por los datos reales; estos son el esqueleto del formato.
const SALIDAS_CABECERA = {
  laborable: [],
  sabado: [],
  festivo: [],
};

// Festivos de Madrid en formato MM-DD y AAAA-MM-DD para los moviles.
const FESTIVOS = [
  "01-01", "01-06", "05-01", "05-02", "05-15", "08-15",
  "10-12", "11-01", "11-09", "12-06", "12-08", "12-25",
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/estado") {
      return new Response("No encontrado", { status: 404 });
    }

    const cabeceras = {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "no-store",
    };

    try {
      const token = await obtenerToken(env);
      const [ida, vuelta, llegada] = await Promise.all([
        estadoParada(token, PARADA_IDA, "ida"),
        estadoParada(token, PARADA_VUELTA, "vuelta"),
        estadoParada(token, PARADA_LLEGADA, "llegada"),
      ]);

      // La API en 3895 solo devuelve salidas. El dato que muestra el panel
      // de la marquesina (llegada del proximo bus) sale de la parada
      // anterior del itinerario. Se cuelga de la vuelta como bloque aparte:
      // 'buses' sigue siendo las salidas, para no romper el panel.
      vuelta.llegadas = llegada;

      if (vuelta.estado === "sin_datos") {
        const teorico = salidasTeoricas();
        if (teorico.length) {
          vuelta.buses = teorico;
          vuelta.estado = "programado";
        }
      }

      if (env.DB) {
        ctx.waitUntil(registrar(env.DB, ida, vuelta, llegada));
      }

      return new Response(
        JSON.stringify({ ts: Date.now(), ida, vuelta }),
        { headers: cabeceras }
      );
    } catch (e) {
      return new Response(
        JSON.stringify({ ts: Date.now(), error: String(e.message || e) }),
        { status: 502, headers: cabeceras }
      );
    }
  },
};

// ---------------------------------------------------------------- token

async function obtenerToken(env) {
  const guardado = await env.KV.get("emt_token", { type: "json" });
  if (guardado && guardado.expira > Date.now() + 30000) {
    return guardado.token;
  }

  const headers = env.EMT_EMAIL
    ? { email: env.EMT_EMAIL, password: env.EMT_PASSWORD }
    : { "X-ClientId": env.EMT_CLIENT_ID, passKey: env.EMT_PASSKEY };

  const r = await fetch(`${BASE}/v1/mobilitylabs/user/login/`, { headers });
  const j = await r.json();
  if (String(j.code) !== "01") {
    throw new Error(`login rechazado: ${j.description || j.code}`);
  }
  const token = j.data[0].accessToken;

  // Vive 900 s; lo damos por bueno 800 para no apurar.
  await env.KV.put(
    "emt_token",
    JSON.stringify({ token, expira: Date.now() + 800000 }),
    { expirationTtl: 900 }
  );
  return token;
}

// ---------------------------------------------------------------- API

async function estadoParada(token, parada, rol) {
  const url = `${BASE}/v3/transport/busemtmad/stops/${parada}/arrives/${LINEA}/`;
  const r = await fetch(url, {
    method: "POST",
    headers: { accessToken: token, "Content-Type": "application/json" },
    body: JSON.stringify({
      cultureInfo: "ES",
      Text_StopRequired_YN: "Y",
      Text_EstimationsRequired_YN: "Y",
      Text_IncidencesRequired_YN: "N",
      DateTime_Referenced_Incidencies_YYYYMMDD: hoyCompacto(),
    }),
  });

  if (!r.ok) return { rol, parada, estado: "error_api", buses: [] };

  const j = await r.json();
  const bloque = (j.data && j.data[0]) || {};
  const arrives = bloque.Arrive || bloque.arrives || [];

  const buses = arrives
    .map((a) => {
      const seg = a.estimateArrive ?? a.busTimeLeft;
      return {
        bus: String(a.bus ?? a.busId ?? ""),
        linea: String(a.line ?? a.lineId ?? ""),
        segundos: typeof seg === "number" ? seg : null,
        distancia: a.DistanceBus ?? a.busDistance ?? null,
      };
    })
    .filter((b) => b.linea === LINEA || b.linea === "")
    .filter((b) => b.segundos !== null)
    .filter((b) => b.segundos < CENTINELA)
    .sort((a, b) => a.segundos - b.segundos);

  // Un mismo vehiculo puede aparecer duplicado entre sondeos.
  const vistos = new Set();
  const unicos = buses.filter((b) => {
    if (b.bus && vistos.has(b.bus)) return false;
    vistos.add(b.bus);
    return true;
  });

  const lejanos = arrives.some((a) => {
    const s = a.estimateArrive ?? a.busTimeLeft;
    return typeof s === "number" && s >= CENTINELA;
  });

  return {
    rol,
    parada,
    estado: unicos.length ? "ok" : lejanos ? "lejano" : "sin_datos",
    buses: unicos.slice(0, 2).map((b) => ({
      segundos: b.segundos,
      lejano: b.segundos > HORIZONTE,
      bus: b.bus,
      fuente: "rt",
    })),
  };
}

// ------------------------------------------------------- horario teorico

function salidasTeoricas() {
  const ahora = enMadrid();
  const minutos = ahora.getHours() * 60 + ahora.getMinutes();
  const tabla = SALIDAS_CABECERA[tipoDeDia(ahora)] || [];

  return tabla
    .filter((m) => m >= minutos)
    .slice(0, 2)
    .map((m) => ({
      segundos: (m - minutos) * 60 - ahora.getSeconds(),
      lejano: false,
      bus: null,
      fuente: "gtfs",
    }));
}

function tipoDeDia(d) {
  const mmdd = `${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")}`;
  if (FESTIVOS.includes(mmdd) || d.getDay() === 0) return "festivo";
  if (d.getDay() === 6) return "sabado";
  return "laborable";
}

function enMadrid() {
  return new Date(
    new Date().toLocaleString("en-US", { timeZone: "Europe/Madrid" })
  );
}

function hoyCompacto() {
  const d = enMadrid();
  return (
    d.getFullYear() +
    String(d.getMonth() + 1).padStart(2, "0") +
    String(d.getDate()).padStart(2, "0")
  );
}

// ---------------------------------------------------------------- registro

async function registrar(db, ...paradas) {
  const ts = Math.floor(Date.now() / 1000);
  const filas = [];
  for (const p of paradas) {
    for (const b of p.buses) {
      if (b.fuente !== "rt") continue;
      filas.push([ts, p.parada, b.bus, b.segundos]);
    }
  }
  if (!filas.length) return;

  const stmt = db.prepare(
    "INSERT INTO estimaciones (ts, parada, bus, segundos) VALUES (?, ?, ?, ?)"
  );
  await db.batch(filas.map((f) => stmt.bind(...f)));
}
