import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

CMC_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

TOP_N = 200   # 1 sola llamada, 1 crédito

DATA_DIR = Path("data") / "cmc"

# Archivos del día actual (se crea uno nuevo cada día)
HOY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
CSV_TOP100 = DATA_DIR / f"market_history_top100_{HOY}.csv"
CSV_EMERGING = DATA_DIR / f"market_history_emerging_{HOY}.csv"

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
# COLUMNAS
# ============================================================

CSV_COLUMNS = [
    "snapshot_id", "timestamp",
    "cmc_id", "name", "symbol", "slug",
    "cmc_rank", "price",
    "percent_change_1h", "percent_change_24h", "percent_change_7d",
    "percent_change_30d", "percent_change_60d", "percent_change_90d",
    "volume_24h", "volume_change_24h",
    "market_cap", "market_cap_dominance",
    "volume_rank_top100", "loser_rank_top100", "gainer_rank_top100",
    "is_top100",
]


# ============================================================
# UTILIDADES
# ============================================================

def get_api_key():
    api_key = os.getenv("CMC_API_KEY")
    if not api_key:
        print("ERROR: No existe CMC_API_KEY.")
        sys.exit(1)
    return api_key


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def create_snapshot_id(timestamp):
    return timestamp.strftime("%Y%m%d_%H%M%S")


# ============================================================
# FETCH
# ============================================================

def fetch_monedas(api_key):
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": api_key}
    params = {
        "start": 1, "limit": TOP_N, "convert": "USD",
        "sort": "market_cap", "sort_dir": "desc",
        "cryptocurrency_type": "all",
    }
    print(f"Consultando CoinMarketCap (limit={TOP_N})...")
    r = requests.get(CMC_API_URL, headers=headers, params=params, timeout=TIMEOUT)
    print(f"HTTP: {r.status_code}")
    if r.status_code != 200:
        print(r.text)
        r.raise_for_status()
    payload = r.json()
    status = payload.get("status", {})
    if status.get("error_code", 0) != 0:
        print("ERROR CMC:", status)
        sys.exit(1)
    data = payload.get("data", [])
    if not data:
        print("ERROR: CMC no devolvió monedas.")
        sys.exit(1)
    print(f"Monedas recibidas: {len(data)}")
    print(f"Credits utilizados: {status.get('credit_count')}")
    return data


# ============================================================
# RANKINGS (solo top 100)
# ============================================================

def calculate_rankings(coins):
    def ch(c):
        q = c.get("quote", {}).get("USD", {})
        return safe_float(q.get("percent_change_24h"))

    def vol(c):
        q = c.get("quote", {}).get("USD", {})
        return safe_float(q.get("volume_24h"))

    losers = sorted(coins, key=lambda c: (ch(c) if ch(c) is not None else float("inf")))
    gainers = sorted(
        coins,
        key=lambda c: (ch(c) if ch(c) is not None else float("-inf")),
        reverse=True,
    )
    volume = sorted(
        coins,
        key=lambda c: (vol(c) if vol(c) is not None else float("-inf")),
        reverse=True,
    )

    return (
        {c["id"]: p for p, c in enumerate(losers, start=1)},
        {c["id"]: p for p, c in enumerate(gainers, start=1)},
        {c["id"]: p for p, c in enumerate(volume, start=1)},
    )


# ============================================================
# CONSTRUIR REGISTROS
# ============================================================

def build_records(coins, timestamp, es_top100=True):
    snapshot_id = create_snapshot_id(timestamp)

    if es_top100:
        loser_rank, gainer_rank, volume_rank = calculate_rankings(coins)
    else:
        loser_rank, gainer_rank, volume_rank = {}, {}, {}

    records = []
    for coin in coins:
        q = coin.get("quote", {}).get("USD", {})
        records.append({
            "snapshot_id": snapshot_id,
            "timestamp": timestamp.isoformat(),
            "cmc_id": coin.get("id"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "slug": coin.get("slug"),
            "cmc_rank": coin.get("cmc_rank"),
            "price": safe_float(q.get("price")),
            "percent_change_1h": safe_float(q.get("percent_change_1h")),
            "percent_change_24h": safe_float(q.get("percent_change_24h")),
            "percent_change_7d": safe_float(q.get("percent_change_7d")),
            "percent_change_30d": safe_float(q.get("percent_change_30d")),
            "percent_change_60d": safe_float(q.get("percent_change_60d")),
            "percent_change_90d": safe_float(q.get("percent_change_90d")),
            "volume_24h": safe_float(q.get("volume_24h")),
            "volume_change_24h": safe_float(q.get("volume_change_24h")),
            "market_cap": safe_float(q.get("market_cap")),
            "market_cap_dominance": safe_float(q.get("market_cap_dominance")),
            "volume_rank_top100": volume_rank.get(coin.get("id")),
            "loser_rank_top100": loser_rank.get(coin.get("id")),
            "gainer_rank_top100": gainer_rank.get(coin.get("id")),
            "is_top100": 1 if es_top100 else 0,
        })
    return records


# ============================================================
# GUARDAR (append al archivo del día)
# ============================================================

def save_records(records, archivo):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = archivo.exists()

    with archivo.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)

    print(f"Guardado: {len(records)} registros → {archivo.name}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 100)
    print("📊 CMC COLLECTOR — TOP 100 + EMERGING 101-200 (v3: archivos diarios)")
    print(f"Archivo del día: {CSV_TOP100.name} y {CSV_EMERGING.name}")
    print("=" * 100)

    api_key = get_api_key()
    timestamp = datetime.now(timezone.utc)
    coins = fetch_monedas(api_key)

    top_100 = [c for c in coins if (c.get("cmc_rank") or 99999) <= 100]
    emerging = [c for c in coins if 100 < (c.get("cmc_rank") or 99999) <= 200]

    print(f"\nDividido: {len(top_100)} top100 | {len(emerging)} emerging")

    save_records(build_records(top_100, timestamp, es_top100=True), CSV_TOP100)
    save_records(build_records(emerging, timestamp, es_top100=False), CSV_EMERGING)

    print()
    print("=" * 100)
    print("✅ CAPTURA FINALIZADA")
    print("=" * 100)


if __name__ == "__main__":
    main()
