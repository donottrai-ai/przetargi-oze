"""
Monitor przetargów OZE - scraping przetargi.gov.pl
"""

import requests
import sqlite3
import os
import logging
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH            = "przetargi_oze.db"

SEARCH_URL = "https://przetargi.gov.pl/index.php"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS przetargi (
            id          TEXT PRIMARY KEY,
            tytul       TEXT,
            zamawiajacy TEXT,
            data_pub    TEXT,
            termin      TEXT,
            url         TEXT,
            dodano      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    return con


def is_new(con, notice_id):
    return con.execute("SELECT 1 FROM przetargi WHERE id=?", (notice_id,)).fetchone() is None


def save(con, p):
    con.execute("""
        INSERT OR IGNORE INTO przetargi (id, tytul, zamawiajacy, data_pub, termin, url)
        VALUES (:id, :tytul, :zamawiajacy, :data_pub, :termin, :url)
    """, p)
    con.commit()


def fetch_notices():
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []
    keywords = ["fotowoltai", "pompa ciepla", "OZE", "energia odnawialna", "magazyn energii"]
    for keyword in keywords:
        try:
            params = {"szukaj": keyword, "strona": 1}
            r = requests.get(SEARCH_URL, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.select("table.lista tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 3:
                    continue
                link = row.find("a", href=True)
                if not link:
                    continue
                url   = "https://przetargi.gov.pl/" + link["href"]
                uid   = link["href"]
                title = link.get_text(strip=True)
                date  = cols[-1].get_text(strip=True) if cols else ""
                results.append(dict(id=uid, tytul=title, zamawiajacy="", data_pub=date, termin="", url=url))
            log.info(f"Slowo '{keyword}': {len(rows)} wierszy")
        except Exception as e:
            log.warning(f"Blad dla '{keyword}': {e}")
    return results


def send_telegram(p):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"[NOWY] {p['tytul']}")
        return
    text = (
        f"🌱 *Nowy przetarg OZE*\n\n"
        f"📋 *{p['tytul']}*\n"
        f"📅 Data: {p['data_pub']}\n"
        f"🔗 [Ogloszenie]({p['url']})"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=10
        )
    except Exception as e:
        log.error(f"Blad Telegram: {e}")


def run():
    log.info("=== Start monitoringu OZE ===")
    con = init_db()
    notices = fetch_notices()
    nowe = 0
    for p in notices:
        if p["id"] and is_new(con, p["id"]):
            save(con, p)
            send_telegram(p)
            nowe += 1
    log.info(f"=== Koniec: {nowe} nowych przetargow ===")
    con.close()


if __name__ == "__main__":
    run()
