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

TOP_N = 100

DATA_DIR = Path("data") / "cmc"
CSV_FILE = DATA_DIR / "market_history.csv"
ROTATION_FILE = DATA_DIR / "last_rotation.txt"
ARCHIVE_DIR = DATA_DIR / "archives"

# Días entre rotaciones
DIAS_ROTACION = 5

# Repo (para construir URL de descarga)
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
        print("Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID)")
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
        contenido = ROTATION_FILE.read_text().strip()
        return datetime.fromisoformat(contenido)
    except Exception:
        return None


def guardar_ultima_rotacion(dt):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ROTATION_FILE.write_text(dt.isoformat())


def inicializar_rotacion():
    """Crea el archivo de rotación la primera vez que corre."""
    if not ROTATION_FILE.exists():
        ahora = datetime.now(timezone.utc)
        guardar_ultima_rotacion(ahora)
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
    delta = ahora - ultima
    return delta >= timedelta(days=DIAS_ROTACION)


def rotar_csv():
    if not CSV_FILE.exists():
        print("No hay CSV para rotar.")
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now(timezone.utc)
    nombre = f"market_history_{ahora.strftime('%Y%m%d_%H%M')}.csv"
    destino = ARCHIVE_DIR / nombre

    CSV_FILE.rename(destino)
    print(f"CSV rotado a: {destino}")

    guardar_ultima_rotacion(ahora)

    tamaño_mb = destino.stat().st_size / (1024 * 1024)

    return {
        "ruta": str(destino),
        "nombre": nombre,
        "tamaño_mb": tamaño_mb,
        "fecha": ahora,
    }


def notificar_rotacion(info):
    """Envía Telegram con el enlace de descarga."""
    if not info:
        return

    url_raw = (
        f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/"
        f"{GITHUB_BRANCH}/data/cmc/archives/{info['nombre']}"
    )
    url_web = (
        f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/blob/"
        f"{GITHUB_BRANCH}/data/cmc/archives/{info['nombre']}"
    )

    fecha_lima = info["fecha"] - timedelta(hours=5)

    msg = (
        f"📦 CMC CACHE LISTO PARA DESCARGAR\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Cierre: {fecha_lima.strftime('%Y-%m-%d %H:%M')} Lima\n"
        f"📄 Archivo: {info['nombre']}\n"
        f"💾 Tamaño: {info['tamaño_mb']:.2f} MB\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 Descarga directa:\n"
        f"{url_raw}\n"
        f"🌐 Ver en GitHub:\n"
        f"{url_web}"
    )

    if enviar_telegram(msg):
        print("Telegram enviado: notificación de rotación")
    else:
        print("Fallo al enviar Telegram")


# ============================================================
# COLUMNAS DEL HISTÓRICO
# ============================================================

CSV_COLUMNS = [
    "snapshot_id",
    "timestamp",

    "cmc_id",
    "name",
    "symbol",
    "slug",

    "cmc_rank",

    "price",

    "percent_change_1h",
    "percent_change_24h",
    "percent_change_7d",
    "percent_change_30d",
    "percent_change_60d",
    "percent_change_90d",

    "volume_24h",
    "volume_change_24h",

    "market_cap",
    "market_cap_dominance",

    "volume_rank_top100",
    "loser_rank_top100",
    "gainer_rank_top100",

    "is_top100",
]


# ============================================================
# UTILIDADES
# ============================================================

def get_api_key():
    api_key = os.getenv("CMC_API_KEY")
    if not api_key:
        print("ERROR: No existe la variable de entorno CMC_API_KEY.")
        sys.exit(1)
    return api_key


def safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def create_snapshot_id(timestamp):
    return timestamp.strftime("%Y%m%d_%H%M%S")


# ============================================================
# OBTENER TOP 100
# ============================================================

def fetch_top_100(api_key):
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": api_key,
    }
    params = {
        "start": 1,
        "limit": TOP_N,
        "convert": "USD",
        "sort": "market_cap",
        "sort_dir": "desc",
        "cryptocurrency_type": "all",
    }
    print("Consultando CoinMarketCap...")
    response = requests.get(CMC_API_URL, headers=headers, params=params, timeout=TIMEOUT)
    print(f"HTTP: {response.status_code}")
    if response.status_code != 200:
        print("Respuesta de CoinMarketCap:")
        print(response.text)
        response.raise_for_status()
    payload = response.json()
    status = payload.get("status", {})
    error_code = status.get("error_code", 0)
    if error_code != 0:
        print("ERROR DE CMC:")
        print(status)
        sys.exit(1)
    data = payload.get("data", [])
    if not data:
        print("ERROR: CMC no devolvió monedas.")
        sys.exit(1)
    print(f"Monedas recibidas: {len(data)}")
    print(f"Credits utilizados: {status.get('credit_count')}")
    return data


# ============================================================
# CALCULAR RANKINGS
# ============================================================

def calculate_rankings(coins):
    def change_24h(coin):
        quote = coin.get("quote", {}).get("USD", {})
        return safe_float(quote.get("percent_change_24h"))

    def volume_24h(coin):
        quote = coin.get("quote", {}).get("USD", {})
        return safe_float(quote.get("volume_24h"))

    losers = sorted(coins, key=lambda c: (change_24h(c) if change_24h(c) is not None else float("inf")))
    gainers = sorted(coins, key=lambda c: (change_24h(c) if change_24h(c) is not None else float("-inf")), reverse=True)
    volume = sorted(coins, key=lambda c: (volume_24h(c) if volume_24h(c) is not None else float("-inf")), reverse=True)

    loser_rank = {c["id"]: p for p, c in enumerate(losers, start=1)}
    gainer_rank = {c["id"]: p for p, c in enumerate(gainers, start=1)}
    volume_rank = {c["id"]: p for p, c in enumerate(volume, start=1)}

    return loser_rank, gainer_rank, volume_rank


# ============================================================
# CONSTRUIR REGISTROS
# ============================================================

def build_records(coins, timestamp):
    snapshot_id = create_snapshot_id(timestamp)
    loser_rank, gainer_rank, volume_rank = calculate_rankings(coins)
    records = []
    for coin in coins:
        quote = coin.get("quote", {}).get("USD", {})
        record = {
            "snapshot_id": snapshot_id,
            "timestamp": timestamp.isoformat(),
            "cmc_id": coin.get("id"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "slug": coin.get("slug"),
            "cmc_rank": coin.get("cmc_rank"),
            "price": safe_float(quote.get("price")),
            "percent_change_1h": safe_float(quote.get("percent_change_1h")),
            "percent_change_24h": safe_float(quote.get("percent_change_24h")),
            "percent_change_7d": safe_float(quote.get("percent_change_7d")),
            "percent_change_30d": safe_float(quote.get("percent_change_30d")),
            "percent_change_60d": safe_float(quote.get("percent_change_60d")),
            "percent_change_90d": safe_float(quote.get("percent_change_90d")),
            "volume_24h": safe_float(quote.get("volume_24h")),
            "volume_change_24h": safe_float(quote.get("volume_change_24h")),
            "market_cap": safe_float(quote.get("market_cap")),
            "market_cap_dominance": safe_float(quote.get("market_cap_dominance")),
            "volume_rank_top100": volume_rank.get(coin.get("id")),
            "loser_rank_top100": loser_rank.get(coin.get("id")),
            "gainer_rank_top100": gainer_rank.get(coin.get("id")),
            "is_top100": 1,
        }
        records.append(record)
    return records


# ============================================================
# GUARDAR CSV
# ============================================================

def save_records(records):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_FILE.exists()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)
    print(f"Guardado: {len(records)} registros")
    print(f"Archivo: {CSV_FILE}")


# ============================================================
# RESUMEN
# ============================================================

def print_summary(records):
    print()
    print("=" * 100)
    print("RESUMEN DE CAPTURA CMC")
    print("=" * 100)
    print(f"Registros: {len(records)}")
    if not records:
        return
    timestamp = records[0]["timestamp"]
    print(f"Timestamp: {timestamp}")
    print(f"Archivo:   {CSV_FILE}")

    print()
    print("TOP 10 MAYORES CAÍDAS 24H")
    print("-" * 100)
    losers = sorted(records, key=lambda x: (x["percent_change_24h"] if x["percent_change_24h"] is not None else float("inf")))
    for position, coin in enumerate(losers[:10], start=1):
        change = coin["percent_change_24h"]
        print(f"{position:2d}. {coin['symbol']:<10} {change:8.2f}% CMC#{coin['cmc_rank']}")

    print()
    print("TOP 10 MAYORES SUBIDAS 24H")
    print("-" * 100)
    gainers = sorted(records, key=lambda x: (x["percent_change_24h"] if x["percent_change_24h"] is not None else float("-inf")), reverse=True)
    for position, coin in enumerate(gainers[:10], start=1):
        change = coin["percent_change_24h"]
        print(f"{position:2d}. {coin['symbol']:<10} {change:8.2f}% CMC#{coin['cmc_rank']}")

    print()
    print("TOP 10 MAYOR VOLUMEN 24H")
    print("-" * 100)
    volume = sorted(records, key=lambda x: (x["volume_24h"] if x["volume_24h"] is not None else float("-inf")), reverse=True)
    for position, coin in enumerate(volume[:10], start=1):
        volume_value = coin["volume_24h"] or 0
        print(f"{position:2d}. {coin['symbol']:<10} ${volume_value:,.0f} CMC#{coin['cmc_rank']}")


# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("=" * 100)
    print("CMC COLLECTOR")
    print("=" * 100)

    # 1) Inicializar rotación si es la primera vez
    inicializar_rotacion()

    # 2) Comprobar si toca rotar
    if toca_rotar():
        print(f"Han pasado {DIAS_ROTACION}+ días. Rotando CSV...")
        info = rotar_csv()
        if info:
            notificar_rotacion(info)
    else:
        ultima = leer_ultima_rotacion()
        if ultima:
            ahora = datetime.now(timezone.utc)
            if ultima.tzinfo is None:
                ultima = ultima.replace(tzinfo=timezone.utc)
            delta = ahora - ultima
            dias_restantes = max(0, DIAS_ROTACION - delta.days)
            print(f"Próxima rotación en ~{dias_restantes} día(s)")

    # 3) Capturar datos
    api_key = get_api_key()
    timestamp = datetime.now(timezone.utc)
    coins = fetch_top_100(api_key)
    records = build_records(coins, timestamp)
    save_records(records)
    print_summary(records)

    print()
    print("=" * 100)
    print("CAPTURA FINALIZADA")
    print("=" * 100)


if __name__ == "__main__":
    main()
