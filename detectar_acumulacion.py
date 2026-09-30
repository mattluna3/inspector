import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

DATA_DIR = Path("data") / "cmc"
CSV_FILE = DATA_DIR / "market_history.csv"
STATE_FILE = DATA_DIR / "acumulacion_state.json"
LOG_FILE = DATA_DIR / "acumulacion_log.jsonl"

# ─── Criterios de detección ───
VOLUME_CHANGE_MIN = 50          # % — volumen sube 50%+ vs ayer
PRICE_FLAT_MAX = 3              # % — precio plano (±3%)
PERCENT_7D_MAX = -10            # % — viene de caída 10%+
VOLUME_24H_MIN = 5_000_000      # USD — liquidez mínima

# ─── Consistencia (evita falsos positivos por un pico aislado) ───
MIN_SNAPSHOTS = 6               # mínimo de snapshots en la ventana
VENTANA_MINUTOS = 120           # últimos 2h
CONSISTENCIA_MIN = 0.5          # 50% de los snapshots deben cumplir

# ─── Anti-spam ───
COOLDOWN_HORAS = 24

GITHUB_USER = "mattluna3"
GITHUB_REPO = "inspector"
GITHUB_BRANCH = "main"

TIMEOUT = 30


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram no configurado")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": msg}
        r = requests.post(url, data=data, timeout=20)
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception as e:
        print(f"Error Telegram: {e}")
        return False


# ============================================================
# UTILIDADES
# ============================================================

def numero(valor):
    if valor is None:
        return None
    try:
        return float(valor)
    except (ValueError, TypeError):
        return None


def parse_ts(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


# ============================================================
# CARGAR CSV
# ============================================================

def cargar_csv():
    if not CSV_FILE.exists():
        print(f"No existe {CSV_FILE}")
        return []

    rows = []
    with CSV_FILE.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Filas cargadas: {len(rows)}")
    return rows


def agrupar_por_symbol(rows):
    """{symbol: [rows ordenadas por timestamp]}"""
    por_symbol = defaultdict(list)
    for row in rows:
        symbol = row.get("symbol")
        if symbol:
            por_symbol[symbol].append(row)

    for symbol in por_symbol:
        por_symbol[symbol].sort(key=lambda r: r.get("timestamp", ""))

    return por_symbol


# ============================================================
# DETECCIÓN
# ============================================================

def ventana_reciente(rows_symbol, ahora, minutos):
    """Devuelve las rows dentro de los últimos X minutos"""
    cutoff = ahora - timedelta(minutes=minutos)
    resultado = []
    for row in rows_symbol:
        ts = parse_ts(row.get("timestamp", ""))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            resultado.append(row)
    return resultado


def cumple_criterios(row):
    """Evalúa si un snapshot cumple los criterios."""
    vol_change = numero(row.get("volume_change_24h"))
    price_24h = numero(row.get("percent_change_24h"))
    price_7d = numero(row.get("percent_change_7d"))
    volume_24h = numero(row.get("volume_24h"))

    if any(v is None for v in [vol_change, price_24h, price_7d, volume_24h]):
        return False

    # Precio plano
    if abs(price_24h) > PRICE_FLAT_MAX:
        return False

    # Volumen anómalo (subida grande)
    if vol_change < VOLUME_CHANGE_MIN:
        return False

    # Viene de caída
    if price_7d > PERCENT_7D_MAX:
        return False

    # Liquidez mínima
    if volume_24h < VOLUME_24H_MIN:
        return False

    return True


def analizar_symbol(symbol, rows_symbol, ahora):
    """Analiza un símbolo y devuelve info si cumple."""
    ventana = ventana_reciente(rows_symbol, ahora, VENTANA_MINUTOS)

    if len(ventana) < MIN_SNAPSHOTS:
        return None

    cumplen = [r for r in ventana if cumple_criterios(r)]

    ratio = len(cumplen) / len(ventana)
    if ratio < CONSISTENCIA_MIN:
        return None

    # Snapshot más reciente de la ventana
    ultimo = ventana[-1]

    return {
        "symbol": symbol,
        "snapshots_ventana": len(ventana),
        "snapshots_cumplen": len(cumplen),
        "ratio": ratio,
        "price": numero(ultimo.get("price")),
        "percent_change_24h": numero(ultimo.get("percent_change_24h")),
        "percent_change_7d": numero(ultimo.get("percent_change_7d")),
        "volume_24h": numero(ultimo.get("volume_24h")),
        "volume_change_24h": numero(ultimo.get("volume_change_24h")),
        "cmc_rank": numero(ultimo.get("cmc_rank")),
        "volume_rank_top100": numero(ultimo.get("volume_rank_top100")),
        "timestamp": ultimo.get("timestamp"),
    }


# ============================================================
# ESTADO (anti-spam)
# ============================================================

def cargar_estado():
    if not STATE_FILE.exists():
        return {"alerts": {}}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"alerts": {}}


def guardar_estado(estado):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def puede_alertar(estado, symbol, ahora_ts):
    ultimo = estado["alerts"].get(symbol)
    if ultimo is None:
        return True
    return (ahora_ts - ultimo) >= COOLDOWN_HORAS * 3600


# ============================================================
# LOG
# ============================================================

def log_acumulacion(registro):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


# ============================================================
# MENSAJE TELEGRAM
# ============================================================

def construir_mensaje(info, ahora_lima):
    vol_24h_m = info["volume_24h"] / 1_000_000

    msg = (
        f"🔍 ACUMULACIÓN SILENCIOSA — {info['symbol']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Precio: ${info['price']:.6f}\n"
        f"📊 Cambio 24h: {info['percent_change_24h']:+.2f}% (plano)\n"
        f"📉 Cambio 7d: {info['percent_change_7d']:+.2f}% (viene de caída)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💧 Volumen 24h: ${vol_24h_m:.2f}M\n"
        f"📈 Volumen cambio 24h: {info['volume_change_24h']:+.2f}%\n"
        f"📊 CMC rank: #{int(info['cmc_rank'])}\n"
        f"📊 Volume rank: #{int(info['volume_rank_top100'])}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Cumple {info['snapshots_cumplen']}/{info['snapshots_ventana']} snapshots "
        f"({info['ratio']*100:.0f}%)\n"
        f"⚠️ Alguien está acumulando en silencio\n"
        f"🕐 {ahora_lima}"
    )

    return msg


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 100)
    print("DETECTOR DE ACUMULACIÓN SILENCIOSA")
    print("=" * 100)

    ahora_utc = datetime.now(timezone.utc)
    ahora_ts = ahora_utc.timestamp()
    ahora_lima = (ahora_utc - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")

    rows = cargar_csv()
    if not rows:
        print("Sin datos para analizar")
        return

    por_symbol = agrupar_por_symbol(rows)
    print(f"Símbolos únicos: {len(por_symbol)}")

    estado = cargar_estado()
    candidatos = []

    for symbol, rows_symbol in por_symbol.items():
        info = analizar_symbol(symbol, rows_symbol, ahora_utc)
        if info is None:
            continue

        if not puede_alertar(estado, symbol, ahora_ts):
            print(f"   ⏭️ {symbol}: cumple pero en cooldown")
            continue

        candidatos.append(info)

    candidatos.sort(key=lambda x: x["volume_change_24h"], reverse=True)

    print(f"\nCandidatos: {len(candidatos)}")

    enviados = 0
    for info in candidatos:
        print(
            f"   ✅ {info['symbol']} | vol {info['volume_change_24h']:+.1f}% | "
            f"price24h {info['percent_change_24h']:+.2f}% | "
            f"7d {info['percent_change_7d']:+.2f}%"
        )

        msg = construir_mensaje(info, ahora_lima)
        if enviar_telegram(msg):
            enviados += 1
            estado["alerts"][info["symbol"]] = ahora_ts
            log_acumulacion({
                "ts": ahora_utc.isoformat(),
                "symbol": info["symbol"],
                "price": info["price"],
                "percent_change_24h": info["percent_change_24h"],
                "percent_change_7d": info["percent_change_7d"],
                "volume_24h": info["volume_24h"],
                "volume_change_24h": info["volume_change_24h"],
            })

    # Limpiar cooldowns viejos (>48h)
    estado["alerts"] = {
        s: ts for s, ts in estado["alerts"].items()
        if (ahora_ts - ts) < 48 * 3600
    }

    guardar_estado(estado)

    print()
    print("=" * 100)
    print(f"Alertas enviadas: {enviados}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
