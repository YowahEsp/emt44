#!/usr/bin/env python3
"""
Sondeo de la linea 44 de EMT Madrid en las paradas 1670 (ida) y 3895 (cabecera).

Dos modos:

    python3 emt_44.py resolver
        Verifica que 1670 y 3895 son las paradas correctas contra el listado
        oficial de la linea, e imprime el itinerario completo de ambos sentidos.

    python3 emt_44.py sondeo [--intervalo 30] [--horas 12]
        Consulta las dos paradas cada N segundos y va escribiendo un CSV.
        La primera respuesta cruda de cada endpoint se guarda entera en
        crudo_*.json para poder inspeccionar el esquema real.

Credenciales por entorno (una de las dos parejas):
    EMT_EMAIL / EMT_PASSWORD
    EMT_CLIENT_ID / EMT_PASSKEY

Solo biblioteca estandar.
"""

import argparse
import csv
import json
import os
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://openapi.emtmadrid.es"
LINEA = "44"
PARADAS = {"ida": "1670", "vuelta": "3895"}

# Nombres esperados, para que 'resolver' avise si EMT renumera una parada.
ESPERADO = {
    "1670": "RUBIO Y GALI",
    "3895": "MARQUES DE VIANA",
}

# Por encima de este valor, estimateArrive no es un tiempo sino un centinela
# de la API ("sin estimacion" / "mas de 20 minutos"). El valor exacto se
# confirma con el log; el umbral es deliberadamente generoso.
CENTINELA = 60 * 60  # 3600 s

SALIDA = pathlib.Path("datos")
CTX = ssl.create_default_context()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _peticion(url, headers, cuerpo=None, metodo=None):
    datos = None
    cabeceras = dict(headers)
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
        cabeceras["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=datos, headers=cabeceras, method=metodo)
    with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def login():
    """Devuelve el accessToken. Vive 900 s y es autoextensible."""
    email = os.environ.get("EMT_EMAIL")
    password = os.environ.get("EMT_PASSWORD")
    client_id = os.environ.get("EMT_CLIENT_ID")
    passkey = os.environ.get("EMT_PASSKEY")

    if email and password:
        headers = {"email": email, "password": password}
    elif client_id and passkey:
        headers = {"X-ClientId": client_id, "passKey": passkey}
    else:
        sys.exit(
            "Faltan credenciales. Exporta EMT_EMAIL/EMT_PASSWORD "
            "o EMT_CLIENT_ID/EMT_PASSKEY."
        )

    r = _peticion(f"{BASE}/v1/mobilitylabs/user/login/", headers)
    if str(r.get("code")) != "01":
        sys.exit(f"Login rechazado: {r.get('description')!r}")
    return r["data"][0]["accessToken"]


def paradas_de_linea(token, sentido):
    """Itinerario ordenado de un sentido. 1 = ida, 2 = vuelta."""
    url = f"{BASE}/v1/transport/busemtmad/lines/{LINEA}/stops/{sentido}/"
    return _peticion(url, {"accessToken": token})


def llegadas(token, parada):
    """Estimaciones de la linea 44 en una parada."""
    url = f"{BASE}/v3/transport/busemtmad/stops/{parada}/arrives/{LINEA}/"
    cuerpo = {
        "cultureInfo": "ES",
        "Text_StopRequired_YN": "Y",
        "Text_EstimationsRequired_YN": "Y",
        "Text_IncidencesRequired_YN": "N",
        "DateTime_Referenced_Incidencies_YYYYMMDD": datetime.now().strftime("%Y%m%d"),
    }
    return _peticion(url, {"accessToken": token}, cuerpo=cuerpo, metodo="POST")


# --------------------------------------------------------------------------
# Normalizacion
# --------------------------------------------------------------------------

def _primer_campo(d, *nombres):
    for n in nombres:
        if n in d and d[n] is not None:
            return d[n]
    return None


def normaliza_llegadas(respuesta):
    """
    Aplana la respuesta a una lista de dicts, tolerando los dos esquemas que
    ha usado la API (v1: busTimeLeft/busId, v3: estimateArrive/bus).
    """
    datos = respuesta.get("data") or []
    if not datos:
        return []
    bloque = datos[0] if isinstance(datos[0], dict) else {}
    arrives = bloque.get("Arrive") or bloque.get("arrives") or []

    salida = []
    for a in arrives:
        segundos = _primer_campo(a, "estimateArrive", "busTimeLeft")
        geom = a.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        salida.append(
            {
                "linea": _primer_campo(a, "line", "lineId"),
                "bus": _primer_campo(a, "bus", "busId"),
                "destino": _primer_campo(a, "destination", "Destination"),
                "segundos": segundos,
                "distancia": _primer_campo(a, "DistanceBus", "busDistance"),
                "lon": coords[0],
                "lat": coords[1],
                "centinela": segundos is not None and segundos >= CENTINELA,
            }
        )
    salida.sort(key=lambda x: (x["segundos"] is None, x["segundos"]))
    return salida


# --------------------------------------------------------------------------
# Modos
# --------------------------------------------------------------------------

def modo_resolver():
    token = login()
    for nombre, sentido in (("ida (Callao -> Marques de Viana)", 1),
                            ("vuelta (Marques de Viana -> Callao)", 2)):
        r = paradas_de_linea(token, sentido)
        datos = (r.get("data") or [{}])[0]
        paradas = datos.get("stops") or []
        print(f"\nSentido {sentido} — {nombre}: {len(paradas)} paradas")
        for i, p in enumerate(paradas, 1):
            pid = str(_primer_campo(p, "stop", "stopId", "node") or "?")
            nom = _primer_campo(p, "name", "stopName") or "?"
            marca = ""
            if pid in PARADAS.values():
                esperado = ESPERADO.get(pid, "")
                ok = esperado.lower() in nom.lower()
                marca = "   <-- NUESTRA" + ("" if ok else "  [!] el nombre no cuadra")
            print(f"  {i:>2}. {pid:>6}  {nom}{marca}")

    encontradas = set()
    for sentido in (1, 2):
        for p in ((paradas_de_linea(token, sentido).get("data") or [{}])[0]
                  .get("stops") or []):
            encontradas.add(str(_primer_campo(p, "stop", "stopId", "node")))
    for rol, pid in PARADAS.items():
        estado = "confirmada" if pid in encontradas else "NO APARECE en la linea 44"
        print(f"\n{rol}: parada {pid} — {estado}")


def modo_sondeo(intervalo, horas):
    SALIDA.mkdir(exist_ok=True)
    fichero = SALIDA / f"sondeo_44_{datetime.now():%Y%m%d_%H%M}.csv"
    campos = ["ts", "hora_local", "parada", "rol", "linea", "bus",
              "destino", "segundos", "centinela", "distancia", "lat", "lon"]

    token = login()
    token_ts = time.time()
    fin = time.time() + horas * 3600
    crudo_guardado = set()
    n = 0

    with fichero.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=campos)
        w.writeheader()

        while time.time() < fin:
            # El token vive 900 s; renovamos con margen.
            if time.time() - token_ts > 600:
                try:
                    token = login()
                    token_ts = time.time()
                except Exception as e:
                    print(f"  aviso: fallo al renovar token: {e}")

            ahora = datetime.now(timezone.utc)
            for rol, parada in PARADAS.items():
                try:
                    bruto = llegadas(token, parada)
                except urllib.error.HTTPError as e:
                    print(f"  {parada}: HTTP {e.code}")
                    continue
                except Exception as e:
                    print(f"  {parada}: {e}")
                    continue

                if parada not in crudo_guardado:
                    (SALIDA / f"crudo_{parada}.json").write_text(
                        json.dumps(bruto, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    crudo_guardado.add(parada)

                filas = normaliza_llegadas(bruto)
                for f in filas:
                    w.writerow(
                        {
                            "ts": int(ahora.timestamp()),
                            "hora_local": datetime.now().strftime("%H:%M:%S"),
                            "parada": parada,
                            "rol": rol,
                            "linea": f["linea"],
                            "bus": f["bus"],
                            "destino": f["destino"],
                            "segundos": f["segundos"],
                            "centinela": int(bool(f["centinela"])),
                            "distancia": f["distancia"],
                            "lat": f["lat"],
                            "lon": f["lon"],
                        }
                    )
                fh.flush()

                if not filas:
                    resumen = "sin buses"
                else:
                    resumen = "  ".join(
                        ("centinela" if f["centinela"]
                         else f"{f['segundos']:>4}s")
                        + f"/{f['bus']}"
                        for f in filas[:3]
                    )
                print(f"{datetime.now():%H:%M:%S}  {rol:<6} {parada}  {resumen}")

            n += 1
            time.sleep(intervalo)

    print(f"\n{n} rondas escritas en {fichero}")


def main():
    ap = argparse.ArgumentParser(description="Sondeo linea 44 EMT Madrid")
    sub = ap.add_subparsers(dest="modo", required=True)
    sub.add_parser("resolver")
    s = sub.add_parser("sondeo")
    s.add_argument("--intervalo", type=int, default=30, help="segundos entre rondas")
    s.add_argument("--horas", type=float, default=12, help="duracion del sondeo")
    a = ap.parse_args()

    if a.modo == "resolver":
        modo_resolver()
    else:
        modo_sondeo(a.intervalo, a.horas)


if __name__ == "__main__":
    main()
