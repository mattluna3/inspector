import csv
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

CMC_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

TOP_N = 200

DATA_DIR = Path("data") / "cmc"
CSV_FILE = DATA_DIR / "market_history_top100.csv"       # top 1-100 (activo)
CSV_EMERGING = DATA_DIR / "market_history_emerging.csv" # top 101-200 (activo)
RADAR_HISTORY_FILE = DATA_DIR / "radar_history.csv"      # historia del radar

DIAS_VENTANA = 4      # hoy + 3 anteriores (para consolidados)
DIAS_RADAR = 15       # 15 días para el radar_history

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
# GUARDAR (append al archivo activo)
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
# ROTACIÓN INTELIGENTE (consolidados)
# ============================================================

def rotar_archivo_activo(csv_path, tipo):
    if not csv_path.exists():
        return

    print(f"  🔄 Rotando {csv_path.name}...")

    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as e:
        print(f"  ⚠️ Error leyendo {csv_path.name}: {e}")
        return

    if len(df) == 0:
        return

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp")

    hoy = datetime.now(timezone.utc).date()
    fecha_corte = hoy - timedelta(days=DIAS_VENTANA - 1)

    df_viejos = df[df["timestamp"].dt.date < fecha_corte]
    df_recientes = df[df["timestamp"].dt.date >= fecha_corte]

    if len(df_viejos) > 0:
        for fecha, grupo in df_viejos.groupby(df_viejos["timestamp"].dt.date):
            archivo_diario = DATA_DIR / f"market_history_{tipo}_{fecha.isoformat()}.csv"
            grupo.to_csv(archivo_diario, index=False)
            print(f"  📁 Día {fecha} extraído → {archivo_diario.name} ({len(grupo):,} filas)")

    df_recientes.to_csv(csv_path, index=False)
    print(f"  ✅ {csv_path.name} conserva {len(df_recientes):,} filas "
          f"(desde {df_recientes['timestamp'].min()} hasta {df_recientes['timestamp'].max()})")


# ============================================================
# ACTUALIZAR RADAR_HISTORY
# ============================================================

def actualizar_radar_history():
    """
    Actualiza el archivo radar_history.csv con datos frescos.
    Solo guarda las monedas del radar (leídas desde RADAR_CONFIG)
    y mantiene los últimos 15 días.
    """
    import json

    radar_config_raw = os.getenv("RADAR_CONFIG")
    if not radar_config_raw:
        print("   ⚠️ RADAR_CONFIG no encontrado. Saltando actualización del radar.")
        return

    try:
        radar_config = json.loads(radar_config_raw)
    except Exception as e:
        print(f"   ⚠️ Error al leer RADAR_CONFIG: {e}")
        return

    monedas_radar = set()
    for key in ["radar_suelos", "radar_techos", "radar_fuerza"]:
        if key in radar_config:
            monedas_radar.update(radar_config[key].keys())

    if not monedas_radar:
        print("   ⚠️ No hay monedas en RADAR_CONFIG. Saltando actualización.")
        return

    print(f"   📊 Actualizando radar_history.csv ({len(monedas_radar)} monedas)...")

    dfs = []

    if RADAR_HISTORY_FILE.exists():
        try:
            df_viejo = pd.read_csv(RADAR_HISTORY_FILE, low_memory=False)
            dfs.append(df_viejo)
            print(f"   ✅ Archivo existente: {len(df_viejo):,} filas")
        except Exception as e:
            print(f"   ⚠️ Error leyendo radar_history.csv: {e}")

    for nombre, archivo in [("top100", CSV_FILE), ("emerging", CSV_EMERGING)]:
        if archivo.exists():
            try:
                df = pd.read_csv(archivo, low_memory=False)
                df = df[df["symbol"].str.upper().isin(monedas_radar)]
                if len(df) > 0:
                    dfs.append(df)
                    print(f"   ✅ {nombre}: {len(df):,} filas del radar")
            except Exception as e:
                print(f"   ⚠️ Error leyendo {nombre}: {e}")

    if not dfs:
        print("   ❌ No hay datos para actualizar.")
        return

    df_total = pd.concat(dfs, ignore_index=True)
    df_total["timestamp"] = pd.to_datetime(df_total["timestamp"], utc=True, errors="coerce", format="mixed")
    df_total = df_total.dropna(subset=["timestamp"])
    df_total = df_total.drop_duplicates(subset=["timestamp", "symbol"], keep="last")

    fecha_corte = datetime.now(timezone.utc) - timedelta(days=DIAS_RADAR)
    df_total = df_total[df_total["timestamp"] >= fecha_corte]

    df_total = df_total.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    df_total.to_csv(RADAR_HISTORY_FILE, index=False)

    print(f"   ✅ radar_history.csv actualizado: {len(df_total):,} filas")
    print(f"   📅 Rango: {df_total['timestamp'].min()} → {df_total['timestamp'].max()}")
    print(f"   📊 Monedas: {df_total['symbol'].nunique()}")


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 100)
    print("📊 CMC COLLECTOR — TOP 100 + EMERGING 101-200 (v4: híbrido 4 días)")
    print("=" * 100)

    api_key = get_api_key()
    timestamp = datetime.now(timezone.utc)
    coins = fetch_monedas(api_key)

    top_100 = [c for c in coins if (c.get("cmc_rank") or 99999) <= 100]
    emerging = [c for c in coins if 100 < (c.get("cmc_rank") or 99999) <= 200]

    print(f"\nDividido: {len(top_100)} top100 | {len(emerging)} emerging")

    # 1. Guardar en el archivo activo
    save_records(build_records(top_100, timestamp, es_top100=True), CSV_FILE)
    save_records(build_records(emerging, timestamp, es_top100=False), CSV_EMERGING)

    # 2. Actualizar radar_history.csv
    print("\n📦 Actualizando radar_history.csv...")
    actualizar_radar_history()

    # 3. Rotar: extraer los días más viejos
    print("\n📦 Rotación de archivos activos...")
    rotar_archivo_activo(CSV_FILE, "top100")
    rotar_archivo_activo(CSV_EMERGING, "emerging")

    print()
    print("=" * 100)
    print("✅ CAPTURA FINALIZADA")
    print("=" * 100)


if __name__ == "__main__":
    main()
