import csv
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

CMC_API_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

TOP_N = 200   # 1 sola llamada, 1 crédito

DATA_DIR = Path("data") / "cmc"
CSV_FILE = DATA_DIR / "market_history_top100.csv"       # top 1-100
CSV_EMERGING = DATA_DIR / "market_history_emerging.csv" # top 101-200
ROTATION_FILE = DATA_DIR / "last_rotation.txt"
ARCHIVE_DIR = DATA_DIR / "archives"

DIAS_ROTACION = 5

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
# ROTACIÓN
# ============================================================

def leer_ultima_rotacion():
    if not ROTATION_FILE.exists():
        return None
    try:
        return datetime.fromisoformat(ROTATION_FILE.read_text().strip())
    except Exception:
        return None


def guardar_ultima_rotacion(dt):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROTATION_FILE.write_text(dt.isoformat())


def inicializar_rotacion():
    if not ROTATION_FILE.exists():
        guardar_ultima_rotacion(datetime.now(timezone.utc))
        print(f"Primera ejecución: próxima rotación en {DIAS_ROTACION} días")
        return True
    return False


def toca_rotar():
    ultima = leer_ultima_rotacion()
    if ultima is None:
        return False
    ahora = datetime.now(timezone.utc)
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    return (ahora - ultima) >= timedelta(days=DIAS_ROTACION)


def rotar_ambos_csv():
    """Rota los 2 CSVs nuevos. NO toca market_history.csv viejo."""
    if not CSV_FILE.exists() and not CSV_EMERGING.exists():
        print("No hay CSVs nuevos para rotar.")
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now(timezone.utc)
    sufijo = ahora.strftime("%Y%m%d_%H%M")

    info = {"fecha": ahora, "archivos": [], "tamaño_total_mb": 0}

    if CSV_FILE.exists():
        destino = ARCHIVE_DIR / f"top100_{sufijo}.csv"
        CSV_FILE.rename(destino)
        t = destino.stat().st_size / (1024 * 1024)
        info["archivos"].append({"nombre": destino.name, "tipo": "top100", "tamaño_mb": t})
        info["tamaño_total_mb"] += t
        print(f"Rotado top100: {destino.name} ({t:.2f} MB)")

    if CSV_EMERGING.exists():
        destino = ARCHIVE_DIR / f"emerging_{sufijo}.csv"
        CSV_EMERGING.rename(destino)
        t = destino.stat().st_size / (1024 * 1024)
        info["archivos"].append({"nombre": destino.name, "tipo": "emerging", "tamaño_mb": t})
        info["tamaño_total_mb"] += t
        print(f"Rotado emerging: {destino.name} ({t:.2f} MB)")

    guardar_ultima_rotacion(ahora)
    return info


def notificar_rotacion(info):
    if not info:
        return

    fecha_lima = info["fecha"] - timedelta(hours=5)
    lineas = []
    for a in info["archivos"]:
        url_raw = (
            f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/"
            f"{GITHUB_BRANCH}/data/cmc/archives/{a['nombre']}"
        )
        lineas.append(f"📄 {a['tipo'].upper()} ({a['tamaño_mb']:.1f} MB)\n{a['nombre']}\n{url_raw}")

    msg = (
        f"📦 CMC CACHE LISTO PARA DESCARGAR\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Cierre: {fecha_lima.strftime('%Y-%m-%d %H:%M')} Lima\n"
        f"💾 Total: {info['tamaño_total_mb']:.2f} MB\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        + "\n\n".join(lineas) +
        f"\n━━━━━━━━━━━━━━━━━━━"
    )

    if enviar_telegram(msg):
        print("Telegram enviado: notificación de rotación")
    else:
        print("Fallo al enviar Telegram")


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
    gainers = sorted(coins, key=lambda c: (ch(c) if ch(c) is not None else float("-inf")), reverse=True)
    volume = sorted(coins, key=lambda c: (vol(c) if vol(c) is not None else float("-inf")), reverse=True)

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
# GUARDAR
# ============================================================

def save_records(records, archivo):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = archivo.exists()
    with archivo.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)
    print(f"Guardado: {len(records)} registros en {archivo.name}")


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 100)
    print("CMC COLLECTOR — TOP 100 + EMERGING 101-200")
    print("=" * 100)

    inicializar_rotacion()

    if toca_rotar():
        print(f"Han pasado {DIAS_ROTACION}+ días. Rotando CSVs nuevos...")
        info = rotar_ambos_csv()
        if info:
            notificar_rotacion(info)
    else:
        ultima = leer_ultima_rotacion()
        if ultima:
            ahora = datetime.now(timezone.utc)
            if ultima.tzinfo is None:
                ultima = ultima.replace(tzinfo=timezone.utc)
            dias_restantes = max(0, DIAS_ROTACION - (ahora - ultima).days)
            print(f"Próxima rotación en ~{dias_restantes} día(s)")

    api_key = get_api_key()
    timestamp = datetime.now(timezone.utc)
    coins = fetch_monedas(api_key)

    top_100 = [c for c in coins if (c.get("cmc_rank") or 99999) <= 100]
    emerging = [c for c in coins if 100 < (c.get("cmc_rank") or 99999) <= 200]

    print(f"\nDividido: {len(top_100)} top100 | {len(emerging)} emerging")

    save_records(build_records(top_100, timestamp, es_top100=True), CSV_FILE)
    save_records(build_records(emerging, timestamp, es_top100=False), CSV_EMERGING)

    print()
    print("=" * 100)
    print("CAPTURA FINALIZADA")
    print("=" * 100)


if __name__ == "__main__":
    main()
