"""
Insider-Trade-Watcher
----------------------
Ueberwacht:
    1) SEC Form-4-Meldungen (US-Firmen-Insider)
    2) optionale selbst konfigurierte RSS/Atom-Feeds fuer europaeische
       Directors'-Dealings-Meldungen
    3) HAUPTFEATURE: Trades von US-Kongressmitgliedern, gescraped von
       capitoltrades.com (kostenlos, kein API-Key)

Neue Eintraege werden per ntfy.sh als Push-Benachrichtigung aufs Handy
geschickt.

Abhaengigkeiten (einmalig installieren):
    pip install -r requirements.txt

Wichtig zu SEC EDGAR:
    Die SEC verlangt einen echten User-Agent-Header mit Kontaktinfo,
    sonst wird der Zugriff blockiert (Fair Access Policy).

Wichtig zu EU/AT-Directors'-Dealings:
    Seit 3.7.2016 veroeffentlicht die FMA diese Meldungen NICHT mehr
    selbst -- das macht jeder Emittent einzeln. Es gibt dafuer keinen
    zentralen Feed. Firmen-Feeds koennen unten bei EU_FEEDS eingetragen
    werden, falls vorhanden.

Wichtig zu Congress-Trades (capitoltrades.com):
    - HAUPTFEATURE dieses Scripts, also besonders wichtig zu verstehen:
      Das ist HTML-Scraping einer Website, KEIN offizielles API. Es gibt
      keinen Vertrag mit der Seite, dass sich an der Struktur nichts
      aendert. Wenn capitoltrades.com ihr Seitenlayout aendert, kann
      dieser Teil ohne Vorwarnung aufhoeren zu funktionieren.
    - Es wird pandas.read_html() benutzt, das die HTML-<table>-Struktur
      der Seite ausliest, statt einzelne CSS-Klassen zu suchen -- das
      ist robuster gegen Style-Aenderungen als klassisches Scraping,
      aber nicht unverwundbar.
    - Sortierung der Seite ist standardmaessig nach "Published"
      (Veroeffentlichungsdatum), nicht nach Handelsdatum -- genau das
      wollen wir fuer "was ist NEU bekannt geworden".
    - Trotzdem gilt: bis zu 45 Tage gesetzliche Meldefrist zwischen
      echtem Trade und Veroeffentlichung, Betrag nur als Spanne (z.B.
      "1K-15K"), kein exakter Dollarbetrag.
    - BEIM ERSTEN ECHTEN LAUF: Schau dir im GitHub-Actions-Log den
      Abschnitt "Gefundene Spalten in der Congress-Tabelle: [...]" an.
      Falls die Titel der Benachrichtigungen komisch aussehen, sag mir
      genau, was dort als Spaltenliste ausgegeben wird.

Konfiguration ueber Umgebungsvariablen:
    SEC_USER_AGENT und NTFY_TOPIC werden aus Umgebungsvariablen
    gelesen, damit sie nicht im (oeffentlichen) Repo-Code stehen. In
    GitHub Actions kommen sie aus den Repository Secrets. Lokal kannst
    du sie vor dem Start setzen, z.B. in PowerShell:
        $env:SEC_USER_AGENT = "Timo Beispielname deine-email@example.com"
        $env:NTFY_TOPIC = "timo-insider-watch-x7k2p9"
    Der Congress-Teil braucht keinen API-Key mehr.
"""

import os
import json
import requests
import feedparser
import pandas as pd

# ---------- Konfiguration ----------

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Timo Beispielname deine-email@example.com"
)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "timo-insider-watch-x7k2p9")

# SEC-EDGAR-Feed fuer aktuelle Form-4-Meldungen, wird von der SEC alle
# 10 Minuten aktualisiert
SEC_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&company=&dateb=&owner=include"
    "&count=100&output=atom"
)

# Optionale zusaetzliche Feeds einzelner europaeischer Firmen (falls
# vorhanden). Leer lassen, wenn nicht genutzt.
EU_FEEDS = [
    # "https://www.beispielfirma.at/investor-relations/rss",
]

# Capitol-Trades-Seite mit den neuesten Congress-Trades, sortiert nach
# Veroeffentlichungsdatum (neueste zuerst)
CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades"

# Nur diese Kongressmitglieder melden (Teilstring-Suche im Namen).
# Leere Liste = alle Mitglieder melden.
CONGRESS_NAME_FILTER = []  # z.B. ["Pelosi", "Gottheimer"]

# Datei, in der bereits gemeldete Eintraege gespeichert werden,
# damit keine doppelten Benachrichtigungen verschickt werden.
SEEN_FILE = "seen_entries.json"

# Nur Meldungen melden, deren Titel einen dieser Ticker enthaelt.
# Gilt fuer alle Quellen. Leere Liste = alles melden.
TICKER_FILTER = []  # z.B. ["AAPL", "TSLA", "MSFT"]

# ---------- Funktionen ----------


def lade_gesehene_eintraege():
    """Liest die Liste bereits gemeldeter Eintrags-IDs von der Festplatte."""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as datei:
        daten = json.load(datei)
    return set(daten)


def speichere_gesehene_eintraege(gesehene_ids):
    """Speichert die Liste bereits gemeldeter Eintrags-IDs."""
    with open(SEEN_FILE, "w", encoding="utf-8") as datei:
        json.dump(list(gesehene_ids), datei)


def hole_sec_eintraege():
    """Ruft den SEC-EDGAR-Feed ab und gibt eine Liste von Eintraegen zurueck."""
    header = {"User-Agent": SEC_USER_AGENT}
    antwort = requests.get(SEC_FEED_URL, headers=header, timeout=15)
    antwort.raise_for_status()
    feed = feedparser.parse(antwort.text)
    return feed.entries


def hole_eu_eintraege():
    """Ruft alle konfigurierten EU-Feeds ab und sammelt deren Eintraege."""
    alle_eintraege = []
    for feed_url in EU_FEEDS:
        feed = feedparser.parse(feed_url)
        alle_eintraege.extend(feed.entries)
    return alle_eintraege


def name_passt_zum_congress_filter(text):
    """Prueft, ob ein Name in der gewuenschten Personen-Liste steht."""
    if len(CONGRESS_NAME_FILTER) == 0:
        return True
    text_klein = text.lower()
    for gesuchter_name in CONGRESS_NAME_FILTER:
        if gesuchter_name.lower() in text_klein:
            return True
    return False


def baue_congress_eintrag(zeile, spalten):
    """Wandelt eine Tabellenzeile von capitoltrades.com in das gemeinsame Format um."""
    werte = []
    for spalte in spalten:
        wert = zeile.get(spalte, "")
        wert_text = str(wert).strip()
        if wert_text != "" and wert_text.lower() != "nan":
            werte.append(wert_text)
    titel = " | ".join(werte)
    eintrag_id = "capitoltrades-" + "-".join(werte)
    return {"id": eintrag_id, "title": titel, "link": CAPITOL_TRADES_URL}


def hole_congress_eintraege():
    """
    HAUPTFEATURE, BEST EFFORT: Scraped die neuesten Congress-Trades von
    capitoltrades.com. Siehe Hinweis im Modul-Docstring oben -- das ist
    keine offizielle Schnittstelle und kann bei einer Layout-Aenderung
    der Seite aufhoeren zu funktionieren.
    """
    header = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    antwort = requests.get(CAPITOL_TRADES_URL, headers=header, timeout=20)
    antwort.raise_for_status()

    tabellen = pd.read_html(antwort.text)
    if len(tabellen) == 0:
        print("Keine Tabelle auf capitoltrades.com gefunden -- Seitenstruktur hat sich vermutlich geaendert.")
        return []

    tabelle = tabellen[0]
    spalten = list(tabelle.columns)
    print("Gefundene Spalten in der Congress-Tabelle:", spalten)

    eintraege = []
    for _, zeile in tabelle.iterrows():
        eintrag = baue_congress_eintrag(zeile, spalten)
        if name_passt_zum_congress_filter(eintrag["title"]):
            eintraege.append(eintrag)
    return eintraege


def eintrag_passt_zum_filter(titel):
    """Prueft, ob ein Eintrag einen der gewuenschten Ticker enthaelt."""
    if len(TICKER_FILTER) == 0:
        return True
    titel_gross = titel.upper()
    for ticker in TICKER_FILTER:
        if ticker.upper() in titel_gross:
            return True
    return False


def sende_benachrichtigung(titel, link):
    """Schickt eine Push-Benachrichtigung ueber ntfy.sh aufs Handy."""
    url = "https://ntfy.sh/" + NTFY_TOPIC
    nachricht = titel
    if link:
        nachricht = nachricht + "\n" + link
    requests.post(url, data=nachricht.encode("utf-8"), timeout=10)


def verarbeite_eintraege(eintraege, gesehene_ids):
    """Geht Eintraege durch, filtert neue heraus und verschickt Benachrichtigungen."""
    for eintrag in eintraege:
        eintrag_id = eintrag.get("id", eintrag.get("link", ""))
        if eintrag_id in gesehene_ids:
            continue
        gesehene_ids.add(eintrag_id)
        titel = eintrag.get("title", "Unbekannte Meldung")
        link = eintrag.get("link", "")
        if eintrag_passt_zum_filter(titel):
            sende_benachrichtigung(titel, link)
            print("Benachrichtigung verschickt:", titel)


def main():
    gesehene_ids = lade_gesehene_eintraege()

    # Jede Quelle einzeln in try/except, damit ein Fehler bei einer
    # Quelle (z.B. dem scraping-basierten Congress-Teil) nicht die
    # anderen, zuverlaessigeren Quellen mit abschiesst.
    quellen = [
        ("SEC", hole_sec_eintraege),
        ("EU", hole_eu_eintraege),
        ("Congress", hole_congress_eintraege),
    ]

    for name, hole_funktion in quellen:
        try:
            eintraege = hole_funktion()
            verarbeite_eintraege(eintraege, gesehene_ids)
        except Exception as fehler:
            print("Fehler bei Quelle", name, ":", fehler)

    speichere_gesehene_eintraege(gesehene_ids)


if __name__ == "__main__":
    main()
