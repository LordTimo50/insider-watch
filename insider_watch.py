"""
Insider-Trade-Watcher
----------------------
Ueberwacht:
    1) SEC Form-4-Meldungen (US-Firmen-Insider)
    2) optionale selbst konfigurierte RSS/Atom-Feeds fuer europaeische
       Directors'-Dealings-Meldungen
    3) Trades von US-Kongressmitgliedern (Senat + Repraesentantenhaus)
       ueber die Financial Modeling Prep (FMP) API
    4) NEU, BEST-EFFORT/FRAGIL: Trumps Periodic Transaction Reports
       (OGE Form 278-T), die als PDFs auf whitehouse.gov landen

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

Wichtig zu Congress-Trades (FMP):
    - Kostenloser FMP-Account noetig (financialmodelingprep.com),
      kostenloser Tier: 250 Calls/Tag.
    - Kongressmitglieder muessen ihre Trades erst innerhalb von bis zu
      45 Tagen melden -- das ist keine Echtzeit-Meldung, sondern die
      Meldung der Meldung.
    - Der gemeldete Betrag ist eine Spanne (z.B. "$1,001 - $15,000"),
      kein exakter Preis.
    - Die genauen JSON-Feldnamen der FMP-API sind aus der
      Dokumentation/einem Drittanbieter-Client abgeleitet, nicht selbst
      live getestet. Falls nach dem ersten Lauf keine oder komische
      Titel ankommen: einmal in der FMP-API-Playground nachsehen, wie
      die Antwort tatsaechlich aussieht, und die Feldnamen in
      baue_congress_eintrag() anpassen.

Wichtig zum Trump-Watcher (whitehouse.gov):
    - Trump meldet ueber OGE Form 278-T (Praesident), NICHT ueber den
      Congress-Mechanismus oben. Es gibt dafuer keine offizielle,
      dokumentierte API.
    - Dieser Watcher versucht es ueber die eingebaute WordPress-REST-
      Suche der Seite (whitehouse.gov laeuft nachweislich auf
      WordPress). Das ist NICHT offiziell dokumentiert und kann
      jederzeit ohne Vorwarnung aufhoeren zu funktionieren (Endpunkt
      abgeschaltet, Struktur geaendert, Rate-Limit, etc.).
    - WICHTIG: Vor dem produktiven Einsatz die URL unten (WH_MEDIA_URL
      + Parameter) einmal im Browser oeffnen und pruefen, ob echtes
      JSON mit Treffern zurueckkommt. Wenn nicht, funktioniert dieser
      Ansatz auf dieser Seite nicht und muesste durch etwas anderes
      ersetzt werden (z.B. manuelles Nachschauen).
    - Auch hier gilt: bis zu 45 Tage Meldefrist, Betrag nur als Spanne.
    - Um den Trump-Watcher zu deaktivieren: die Zeile mit
      "Whitehouse-Trump" in der QUELLEN-Liste in main() entfernen oder
      auskommentieren.

Konfiguration ueber Umgebungsvariablen:
    SEC_USER_AGENT, NTFY_TOPIC und FMP_API_KEY werden aus
    Umgebungsvariablen gelesen, damit sie nicht im (oeffentlichen)
    Repo-Code stehen. In GitHub Actions kommen sie aus den Repository
    Secrets. Lokal kannst du sie vor dem Start setzen, z.B. in
    PowerShell:
        $env:SEC_USER_AGENT = "Timo Beispielname deine-email@example.com"
        $env:NTFY_TOPIC = "timo-insider-watch-x7k2p9"
        $env:FMP_API_KEY = "dein-fmp-api-key"
    Ohne gesetzte Umgebungsvariable wird jeweils der Platzhalter unten
    benutzt. Der Trump-Watcher braucht keinen API-Key.
"""

import os
import json
import requests
import feedparser

# ---------- Konfiguration ----------

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Timo Beispielname deine-email@example.com"
)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "timo-insider-watch-x7k2p9")

FMP_API_KEY = os.environ.get("FMP_API_KEY", "DEIN_FMP_API_KEY")

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

# FMP-Endpunkte fuer die neuesten Senat- und Repraesentantenhaus-Meldungen
FMP_SENATE_URL = "https://financialmodelingprep.com/stable/senate-latest"
FMP_HOUSE_URL = "https://financialmodelingprep.com/stable/house-latest"

# Nur diese Kongressmitglieder melden (Teilstring-Suche im Namen).
# Leere Liste = alle Mitglieder melden.
CONGRESS_NAME_FILTER = []  # z.B. ["Pelosi", "McConnell"]

# Unoffizielle WordPress-Media-Suche von whitehouse.gov (siehe Hinweis
# oben zur Zuverlaessigkeit)
WH_MEDIA_URL = "https://www.whitehouse.gov/wp-json/wp/v2/media"

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


def name_passt_zum_congress_filter(name):
    """Prueft, ob ein Name in der gewuenschten Personen-Liste steht."""
    if len(CONGRESS_NAME_FILTER) == 0:
        return True
    name_klein = name.lower()
    for gesuchter_name in CONGRESS_NAME_FILTER:
        if gesuchter_name.lower() in name_klein:
            return True
    return False


def baue_congress_eintrag(daten):
    """Wandelt eine einzelne FMP-Meldung in das gemeinsame Eintrags-Format um."""
    name = daten.get("representative", "Unbekannt")
    ticker = daten.get("ticker", "?")
    art = daten.get("transaction", "?")
    betrag = daten.get("amount", "?")
    datum = daten.get("transactionDate", "?")
    transaktions_id = daten.get("transactionId", name + ticker + datum + betrag)
    eintrag_id = "congress-" + str(transaktions_id)
    titel = (
        name + ": " + art + " " + ticker
        + " am " + datum + " (Betrag: " + betrag + ")"
    )
    return {"id": eintrag_id, "title": titel, "link": ""}


def hole_congress_eintraege():
    """Ruft die neuesten Senat- und Repraesentantenhaus-Meldungen von FMP ab."""
    if FMP_API_KEY == "DEIN_FMP_API_KEY":
        print("FMP_API_KEY nicht gesetzt -- Congress-Abfrage wird uebersprungen.")
        return []

    eintraege = []
    for url in (FMP_SENATE_URL, FMP_HOUSE_URL):
        parameter = {"page": 0, "limit": 100, "apikey": FMP_API_KEY}
        antwort = requests.get(url, params=parameter, timeout=15)
        antwort.raise_for_status()
        daten_liste = antwort.json()
        for daten in daten_liste:
            name = daten.get("representative", "Unbekannt")
            if name_passt_zum_congress_filter(name):
                eintraege.append(baue_congress_eintrag(daten))
    return eintraege


def hole_trump_eintraege():
    """
    BEST-EFFORT/FRAGIL: Sucht ueber die WordPress-Media-API von
    whitehouse.gov nach neuen PDF-Uploads von Trumps Periodic
    Transaction Reports. Siehe Hinweis im Modul-Docstring oben --
    das ist keine offizielle Schnittstelle und kann jederzeit brechen.
    """
    parameter = {
        "search": "Trump Periodic Transaction Report",
        "orderby": "date",
        "order": "desc",
        "per_page": 20,
    }
    antwort = requests.get(WH_MEDIA_URL, params=parameter, timeout=15)
    antwort.raise_for_status()
    medien_liste = antwort.json()

    eintraege = []
    for medium in medien_liste:
        titel_objekt = medium.get("title", {})
        titel_text = titel_objekt.get("rendered", "")
        if "trump" not in titel_text.lower():
            continue
        link = medium.get("source_url", "")
        medium_id = "whitehouse-" + str(medium.get("id", link))
        eintraege.append({"id": medium_id, "title": titel_text, "link": link})
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
    # Quelle (z.B. dem fragilen Whitehouse-Watcher) nicht die anderen,
    # zuverlaessigeren Quellen mit abschiesst.
    quellen = [
        ("SEC", hole_sec_eintraege),
        ("EU", hole_eu_eintraege),
        ("Congress", hole_congress_eintraege),
        ("Whitehouse-Trump", hole_trump_eintraege),
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
