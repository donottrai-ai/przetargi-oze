"""
Monitor przetargów OZE - BZP API (ezamowienia.gov.pl)
Uruchamiaj co godzinę (cron / GitHub Actions)
Wymagania: pip install requests python-telegram-bot schedule
"""

import requests
import sqlite3
import json
import os
import logging
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# KONFIGURACJA – uzupełnij przed uruchomieniem
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")   # token od @BotFather
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")     # twoje chat_id
DB_PATH            = "przetargi_oze.db"
API_BASE           = "https://ezamowienia.gov.pl/mo-board/api/v1/Board/GetNoticeList"

# Kody CPV branży OZE
CPV_OZE = [
    "09331000",  # panele słoneczne
    "09332000",  # instalacje słoneczne
    "09330000",  # energia słoneczna ogólnie
    "09300000",  # energia elektryczna, cieplna, słoneczna
    "45261215",  # pokrycia dachowe fotowoltaika
    "42511110",  # pompy ciepła
    "09310000",  # elektryczność
    "45315300",  # instalacje elektroenergetyczne
    "09340000",  # paliwa gazowe (biogaz)
    "09111400",  # biomasa
]

# Słowa kluczowe jako dodatkowy filtr (tytuł ogłoszenia)
KEYWORDS_OZE = [
    "fotowoltai", "panele słoneczne", "pompa ciepła", "pompy ciepła",
    "energia odnawialna", "OZE", "turbina wiatrowa", "farma wiatrowa",
    "magazyn energii", "mikroinstalacja", "instalacja PV",
    "elektrownia słoneczna", "biomasa", "biogaz", "geotermia",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# BAZA DANYCH
# ─────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS przetargi (
            id          TEXT PRIMARY KEY,
            tytul       TEXT,
            zamawiajacy TEXT,
            cpv         TEXT,
            data_pub    TEXT,
            termin      TEXT,
            url         TEXT,
            dodano      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    return con


def is_new(con, notice_id: str) -> bool:
    row = con.execute("SELECT 1 FROM przetargi WHERE id=?", (notice_id,)).fetchone()
    return row is None


def save(con, p: dict):
    con.execute("""
        INSERT OR IGNORE INTO przetargi (id, tytul, zamawiajacy, cpv, data_pub, termin, url)
        VALUES (:id, :tytul, :zamawiajacy, :cpv, :data_pub, :termin, :url)
    """, p)
    con.commit()


# ─────────────────────────────────────────────
# API BZP
# ─────────────────────────────────────────────
def fetch_notices(cpv_prefix: str, page: int = 0, page_size: int = 20) -> list:
    """Pobiera ogłoszenia dla danego kodu CPV."""
    params = {
        "mainCpvCode": cpv_prefix,
        "pageNumber": page,
        "pageSize": page_size,
        "noticePublicationDateFrom": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
    }
    try:
        r = requests.get(API_BASE, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("notices", []) or []
    except Exception as e:
        log.warning(f"Błąd API dla CPV {cpv_prefix}: {e}")
        return []


def matches_keywords(title: str) -> bool:
    """Zwraca True jeśli tytuł zawiera choć jedno słowo kluczowe OZE."""
    t = title.lower()
    return any(k.lower() in t for k in KEYWORDS_OZE)


def parse_notice(raw: dict) -> dict:
    """Mapuje surowy JSON na słownik aplikacji."""
    notice_id = raw.get("noticeNumber") or raw.get("id", "")
    title     = raw.get("orderName") or raw.get("subject", "Brak tytułu")
    buyer     = raw.get("buyerName") or raw.get("contractingAuthority", "")
    cpv       = raw.get("mainCpvCode", "")
    pub_date  = raw.get("publicationDate", "")[:10] if raw.get("publicationDate") else ""
    deadline  = raw.get("submissionDeadline", "")[:10] if raw.get("submissionDeadline") else ""
    url       = f"https://ezamowienia.gov.pl/mp-client/search/list/{notice_id}"
    return dict(id=notice_id, tytul=title, zamawiajacy=buyer,
                cpv=cpv, data_pub=pub_date, termin=deadline, url=url)


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(p: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[NOWY] {p['tytul']} | {p['zamawiajacy']}")
        return

    text = (
        f"🌱 *Nowy przetarg OZE*\n\n"
        f"📋 *{p['tytul']}*\n"
        f"🏛 {p['zamawiajacy']}\n"
        f"📅 Publikacja: {p['data_pub']}\n"
        f"⏰ Termin składania: {p['termin']}\n"
        f"🔖 CPV: {p['cpv']}\n"
        f"🔗 [Ogłoszenie]({p['url']})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        log.error(f"Błąd Telegram: {e}")


# ─────────────────────────────────────────────
# GŁÓWNA PĘTLA
# ─────────────────────────────────────────────
def run():
    log.info("=== Start monitoringu OZE ===")
    con = init_db()
    nowe = 0

    for cpv in CPV_OZE:
        notices = fetch_notices(cpv)
        log.info(f"CPV {cpv}: {len(notices)} ogłoszeń")

        for raw in notices:
            p = parse_notice(raw)
            if not p["id"]:
                continue
            # Filtrowanie: nowe + pasuje do CPV lub słów kluczowych
            if is_new(con, p["id"]):
                if matches_keywords(p["tytul"]) or p["cpv"].startswith(tuple(CPV_OZE)):
                    save(con, p)
                    send_telegram(p)
                    nowe += 1

    log.info(f"=== Koniec: {nowe} nowych przetargów ===")
    con.close()


if __name__ == "__main__":
    run()
