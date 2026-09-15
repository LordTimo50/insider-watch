"""
Insider-Trade-Watcher
----------------------
Ueberwacht:
    1) SEC Form-4-Meldungen (US-Firmen-Insider)
    2) optionale selbst konfigurierte RSS/Atom-Feeds fuer europaeische
       Directors'-Dealings-Meldungen
    3) Trades von US-Kongressmitgliedern, gescraped von
       capitoltrades.com (kostenlos, kein API-Key)

Neue Eintraege werden per ntfy.sh als Push-Benachrichtigung aufs Handy
geschickt, im Format "Name: TICKER (Betrag)".

Abhaengigkeiten (einmalig installieren):
    pip install -r requirements.txt

DIE WICHTIGSTEN SCHRAUBEN ZUM EINSTELLEN (siehe Konfiguration unten):
    WATCHLIST_NAMEN            -- Politiker, deren Trades IMMER durchkommen
    MINDESTBETRAG_USD          -- ab welcher Groesse ueberhaupt gemeldet wird
    RELEVANTE_TRANSAKTIONSCODES-- welche SEC-Transaktionsarten zaehlen
    NUR_KAEUFE_CONGRESS        -- bei Congress nur Kaeufe statt auch Verkaeufe
    CONGRESS_NAME_FILTER       -- nur bestimmte Politiker
    TICKER_FILTER              -- nur bestimmte Aktien

Wichtig zu SEC EDGAR:
    - Die SEC verlangt einen echten User-Agent-Header mit Kontaktinfo,
      sonst wird der Zugriff blockiert (Fair Access Policy).
    - Die SEC erlaubt maximal 10 Anfragen pro Sekunde. Da pro NEUER
      Meldung zwei Zusatzanfragen noetig sind (index.json + XML), wird
      vor jeder SEC-Anfrage eine kurze Pause eingelegt (siehe
      SEC_PAUSE_SEKUNDEN).
    - Fuer Ticker/Name/Betrag wird pro NEUER Form-4-Meldung das
      eigentliche XML-Dokument geladen. Der Dateiname darin variiert je
      nach Filing-Software, darum wird er nicht geraten, sondern aus
      index.json gelesen. Das passiert nur fuer Eintraege, die noch
      nicht in seen_entries.json stehen.
    - Der SEC-Feed liefert durch Praefix-Matching auch andere Formulare,
      die mit "4" beginnen (424B2, 424B5, 425). Das sind Prospekt- und
      Uebernahme-Meldungen, keine Insider-Transaktionen -- sie werden
      erkannt und uebersprungen.
    - Bekannte kleine Einschraenkung: Eine einzelne Form-4-Meldung kann
      im Feed doppelt auftauchen (einmal aus Sicht des Insiders, einmal
      aus Sicht der Firma) und dadurch zweimal eine inhaltlich
      identische Benachrichtigung ausloesen. Nicht behoben, da selten
      und harmlos.

Wichtig zum Betragsfilter:
    - Bei SEC ist der Betrag exakt (Stueckzahl mal Preis pro Stueck),
      der Vergleich mit MINDESTBETRAG_USD ist also eindeutig.
    - Bei Congress gibt es KEINEN exakten Betrag, sondern nur eine
      Spanne wie "15K - 50K" (gesetzlich so vorgesehen). Fuer den
      Vergleich wird daraus die OBERGRENZE genommen, damit ein
      moeglicherweise grosser Trade nicht faelschlich rausfliegt.
      Beispiel: "50K - 100K" wird als 100.000 gewertet.
    - Laesst sich eine Congress-Spanne nicht lesen (unerwartetes
      Format), wird die Meldung DURCHGELASSEN statt still verworfen,
      und im Log erscheint ein Hinweis. Lieber eine Meldung zu viel als
      eine verpasste, die wegen eines Formatfehlers verschwindet.

Wichtig zu EU/AT-Directors'-Dealings:
    Seit 3.7.2016 veroeffentlicht die FMA diese Meldungen NICHT mehr
    selbst -- das macht jeder Emittent einzeln. Es gibt dafuer keinen
    zentralen Feed. Firmen-Feeds koennen unten bei EU_FEEDS eingetragen
    werden, falls vorhanden. EU-Eintraege werden vom Betragsfilter
    nicht erfasst, weil ihr Format unbekannt ist.

Wichtig zu Congress-Trades (capitoltrades.com):
    - Das ist HTML-Scraping einer Website, KEIN offizielles API. Wenn
      capitoltrades.com ihr Seitenlayout aendert, kann dieser Teil ohne
      Vorwarnung aufhoeren zu funktionieren.
    - Bekanntes Problem: GitHub-Actions-Runner-IPs werden von der Seite
      oft mit 429 (Too Many Requests) abgewiesen, waehrend normale
      Heim-IPs durchkommen. Bewusst so akzeptiert -- dieser Teil laeuft
      "best effort" und faellt bei Bedarf aus, ohne die anderen
      Quellen mitzureissen.
    - Es gilt: bis zu 45 Tage gesetzliche Meldefrist zwischen echtem
      Trade und Veroeffentlichung.

Konfiguration ueber Umgebungsvariablen:
    SEC_USER_AGENT und NTFY_TOPIC werden aus Umgebungsvariablen
    gelesen, damit sie nicht im (oeffentlichen) Repo-Code stehen. In
    GitHub Actions kommen sie aus den Repository Secrets.
"""

import os
import json
import time
import re
import requests
import feedparser
import pandas as pd
import xml.etree.ElementTree as ET

# ---------- Konfiguration ----------

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Timo Beispielname deine-email@example.com"
)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "timo-insider-watch-x7k2p9")

# Nur Transaktionen ab diesem Dollarbetrag werden gemeldet.
# Bei Congress wird die Obergrenze der Spanne verglichen (siehe oben).
# Zum Abschalten des Filters: auf 0 setzen.
MINDESTBETRAG_USD = 100000

# Welche SEC-Transaktionsarten (transactionCode im Form-4-XML) gemeldet
# werden:
#   P = offener Marktkauf durch den Insider
#   S = offener Marktverkauf durch den Insider
# Aktuell nur Kaeufe. Willst du Verkaeufe auch sehen: ["P", "S"].
# Willst du wirklich alles (auch Zuteilungen A, Steuereinbehalte F,
# Optionsausuebungen M): leere Liste [] eintragen.
RELEVANTE_TRANSAKTIONSCODES = ["P"]

# Bei Congress nur Kaeufe melden. Auf False setzen, um auch Verkaeufe
# zu bekommen.
NUR_KAEUFE_CONGRESS = True

# SEC-EDGAR-Feed fuer aktuelle Form-4-Meldungen, wird von der SEC alle
# 10 Minuten aktualisiert
SEC_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=4&company=&dateb=&owner=include"
    "&count=100&output=atom"
)

# Pause vor jeder SEC-Anfrage. Die SEC erlaubt 10 Anfragen pro Sekunde;
# 0,15 Sekunden Pause heisst rund 6-7 Anfragen pro Sekunde, also
# komfortabel unter dem Limit.
SEC_PAUSE_SEKUNDEN = 0.15

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

# Maximale Anzahl gemerkter Eintraege. Aeltere werden verworfen, weil
# sie ohnehin nicht mehr im Feed auftauchen.
MAX_GESPEICHERTE_EINTRAEGE = 2000

# Datei, in der pro Quelle gezaehlt wird, wie oft sie hintereinander
# fehlgeschlagen ist (fuer die Stoerungs-Benachrichtigung).
ERROR_STATE_FILE = "error_state.json"

# Ab so vielen Fehlschlaegen hintereinander wird EINMAL eine
# Stoerungsmeldung aufs Handy geschickt.
FEHLER_SCHWELLE = 10

# Ab diesem Dollarbetrag wird mit hoher Prioritaet verschickt (kommt
# auch im Stumm-Modus durch).
GROSSER_BETRAG_SCHWELLE = 1000000

# Nur Meldungen melden, deren Titel einen dieser Ticker enthaelt.
# Leere Liste = alles melden.
TICKER_FILTER = []  # z.B. ["AAPL", "TSLA", "MSFT"]

# ---------- Watchlist: besonders beobachtete Kongressmitglieder ----------
#
# Trades dieser Personen werden IMMER gemeldet: sie umgehen den
# Mindestbetrag UND die Nur-Kaeufe-Regel, bekommen einen Stern, hoechste
# Prioritaet (kommt auch im Stumm-Modus durch) und ein "TOP-TRADER"-
# Praefix im Text.
#
# Abgeglichen wird per Teilstring im Namen, also reicht der Nachname.
#
# ACHTUNG, DATENLAGE (Stand der Recherche, Zahlen fuer das Jahr 2025):
# "Meiste Trades" und "groesster Gewinn" sind ZWEI VERSCHIEDENE Listen
# mit kaum Ueberschneidung. Such dir aus, was dich interessiert:
#
#   Meiste Transaktionen 2025:
#     Ro Khanna        -- ueber 4.100 Trades, 5x mehr als der Zweite
#     Josh Gottheimer  -- durchgehend hohe Frequenz ueber Jahre
#
#   Groesstes Dollarvolumen 2025 (Quellen widersprechen sich teils,
#   weil die Betraege nur als Spannen gemeldet werden):
#     Richard Blumenthal -- rund 80 Mio. $
#     Michael McCaul     -- rund 75 Mio. $
#     Ro Khanna          -- rund 56 Mio. $
#     Nancy Pelosi       -- rund 52 Mio. $ bei nur 19 Trades
#                           (Konzentration statt Masse)
#
#   Beste Rendite 2025 (laut Unusual Whales):
#     Warren Davidson  -- +78,8 %
#     Donald Norcross  -- +70,8 %
#     Terri Sewell     -- +67,9 %
#
# HINWEIS ZUR HALTBARKEIT: Nancy Pelosis Amtszeit endet am 3.1.2027,
# danach taucht sie in keiner Meldung mehr auf. Renditelisten werden
# jaehrlich neu erstellt -- diese Liste also gelegentlich pruefen.
WATCHLIST_NAMEN = [
    "Khanna",      # mit Abstand aktivster Trader im Kongress
    "Pelosi",      # bekannteste Investorin, grosse konzentrierte Positionen
    "Gottheimer",  # dauerhaft hohes Handelsvolumen
]

# ---------- Hilfsfunktionen fuer gespeicherte Zustaende ----------


def lade_gesehene_eintraege():
    """
    Liest die bereits gemeldeten Eintrags-IDs.
    Gibt eine Liste (Reihenfolge = aelteste zuerst) und ein Set
    (fuer schnelles Nachschlagen) zurueck.
    """
    if not os.path.exists(SEEN_FILE):
        return [], set()
    with open(SEEN_FILE, "r", encoding="utf-8") as datei:
        daten = json.load(datei)
    return list(daten), set(daten)


def speichere_gesehene_eintraege(gesehene_liste):
    """
    Speichert die Eintrags-IDs und kuerzt dabei auf die neuesten
    MAX_GESPEICHERTE_EINTRAEGE Eintraege.
    """
    gekuerzt = gesehene_liste[-MAX_GESPEICHERTE_EINTRAEGE:]
    with open(SEEN_FILE, "w", encoding="utf-8") as datei:
        json.dump(gekuerzt, datei)
    print("Gespeicherte Eintraege:", len(gekuerzt))


def lade_fehlerzaehler():
    """Liest, wie oft jede Quelle zuletzt hintereinander fehlgeschlagen ist."""
    if not os.path.exists(ERROR_STATE_FILE):
        return {}
    with open(ERROR_STATE_FILE, "r", encoding="utf-8") as datei:
        return json.load(datei)


def speichere_fehlerzaehler(fehlerzaehler):
    """Speichert den Fehlerzaehler-Stand."""
    with open(ERROR_STATE_FILE, "w", encoding="utf-8") as datei:
        json.dump(fehlerzaehler, datei)


# ---------- Benachrichtigung ----------


def sende_benachrichtigung(titel, tags=None, prioritaet=None):
    """
    Schickt eine Push-Benachrichtigung ueber ntfy.sh aufs Handy.
    tags ist eine Liste von ntfy-Tags, prioritaet z.B. "high".
    """
    url = "https://ntfy.sh/" + NTFY_TOPIC
    header = {}
    if tags:
        header["Tags"] = ",".join(tags)
    if prioritaet:
        header["Priority"] = prioritaet
    requests.post(url, data=titel.encode("utf-8"), headers=header, timeout=10)


# ---------- SEC ----------


def sec_anfrage(url):
    """
    Fuehrt eine Anfrage an die SEC aus und haelt dabei die von der SEC
    geforderte Ratenbegrenzung ein (max. 10 Anfragen pro Sekunde).
    """
    time.sleep(SEC_PAUSE_SEKUNDEN)
    header = {"User-Agent": SEC_USER_AGENT}
    antwort = requests.get(url, headers=header, timeout=15)
    antwort.raise_for_status()
    return antwort


def hole_sec_eintraege():
    """Ruft den SEC-EDGAR-Feed ab und gibt einfache Eintraege zurueck (ohne teure Detail-Requests)."""
    antwort = sec_anfrage(SEC_FEED_URL)
    feed = feedparser.parse(antwort.text)

    eintraege = []
    for feed_eintrag in feed.entries:
        eintraege.append({
            "id": feed_eintrag.get("id", feed_eintrag.get("link", "")),
            "title": feed_eintrag.get("title", "Unbekannte Meldung"),
            "link": feed_eintrag.get("link", ""),
        })
    return eintraege


def ist_form4_eintrag(titel):
    """
    Prueft, ob ein SEC-Feed-Eintrag wirklich ein Form 4 ist. Der Feed
    liefert durch Praefix-Matching auch 424B2/424B5/425 usw., die alle
    mit '4' anfangen, aber keine Insider-Transaktionen sind.
    """
    return titel.startswith("4 - ") or titel.startswith("4/A - ")


def hole_form4_xml_url(index_link):
    """
    Findet die echte XML-Dokument-URL einer Form-4-Meldung ueber die
    index.json der Filing-Directory, statt einen Dateinamen zu raten
    (der je nach verwendeter Filing-Software unterschiedlich ist).
    """
    ordner_url = index_link.rsplit("/", 1)[0]
    antwort = sec_anfrage(ordner_url + "/index.json")
    daten = antwort.json()
    dateien = daten.get("directory", {}).get("item", [])
    for datei in dateien:
        name = datei.get("name", "")
        if name.endswith(".xml"):
            return ordner_url + "/" + name
    return None


def hole_form4_details(index_link):
    """
    Laedt das eigentliche Form-4-XML-Dokument und extrahiert Name,
    Ticker, Transaktionscode und den Dollarbetrag der ersten
    Transaktion. Gibt None zurueck, wenn nichts Verwertbares gefunden
    wird (z.B. reine Bestandsmeldung ohne Transaktion oder fehlender
    Preis bei Schenkungen).
    """
    xml_url = hole_form4_xml_url(index_link)
    if xml_url is None:
        return None

    antwort = sec_anfrage(xml_url)
    baum = ET.fromstring(antwort.content)

    ticker_element = baum.find("./issuer/issuerTradingSymbol")
    ticker = ticker_element.text.strip() if ticker_element is not None and ticker_element.text else "?"

    name_element = baum.find("./reportingOwner/reportingOwnerId/rptOwnerName")
    name = name_element.text.strip() if name_element is not None and name_element.text else "Unbekannt"

    transaktion = baum.find("./nonDerivativeTable/nonDerivativeTransaction")
    if transaktion is None:
        return None

    code_element = transaktion.find("./transactionCoding/transactionCode")
    code = code_element.text.strip() if code_element is not None and code_element.text else "?"

    shares_element = transaktion.find("./transactionAmounts/transactionShares/value")
    preis_element = transaktion.find("./transactionAmounts/transactionPricePerShare/value")
    if shares_element is None or preis_element is None or not shares_element.text or not preis_element.text:
        return None

    shares = float(shares_element.text)
    preis = float(preis_element.text)
    betrag = shares * preis

    return {"name": name, "ticker": ticker, "betrag": betrag, "code": code}


def anreichere_sec_eintrag(eintrag):
    """
    Wird nur fuer NEUE SEC-Eintraege aufgerufen (siehe verarbeite_eintraege).
    Gibt None zurueck, wenn der Eintrag nicht gemeldet werden soll --
    also bei Nicht-Form-4-Formularen, bei nicht gewuenschten
    Transaktionsarten und bei zu kleinen Betraegen.
    """
    roh_titel = eintrag.get("title", "")
    link = eintrag.get("link", "")

    if not ist_form4_eintrag(roh_titel) or not link:
        return None

    try:
        details = hole_form4_details(link)
    except Exception as fehler:
        print("Form-4-Details konnten nicht geladen werden fuer", link, ":", fehler)
        return None

    if details is None:
        return None

    code = details["code"]
    if len(RELEVANTE_TRANSAKTIONSCODES) > 0 and code not in RELEVANTE_TRANSAKTIONSCODES:
        print("Uebersprungen (Transaktionsart", code, "):", details["name"], details["ticker"])
        return None

    betrag = details["betrag"]
    if betrag < MINDESTBETRAG_USD:
        print("Uebersprungen (zu klein: ${:,.0f}):".format(betrag), details["name"], details["ticker"])
        return None

    betrag_text = "${:,.0f}".format(betrag)
    eintrag["title"] = details["name"] + ": " + details["ticker"] + " (" + betrag_text + ")"

    if code == "P":
        eintrag["tags"] = ["chart_with_upwards_trend"]
    elif code == "S":
        eintrag["tags"] = ["chart_with_downwards_trend"]

    if betrag >= GROSSER_BETRAG_SCHWELLE:
        eintrag["prioritaet"] = "high"

    return eintrag


# ---------- EU ----------


def hole_eu_eintraege():
    """Ruft alle konfigurierten EU-Feeds ab und sammelt deren Eintraege."""
    alle_eintraege = []
    for feed_url in EU_FEEDS:
        feed = feedparser.parse(feed_url)
        for feed_eintrag in feed.entries:
            alle_eintraege.append({
                "id": feed_eintrag.get("id", feed_eintrag.get("link", "")),
                "title": feed_eintrag.get("title", "Unbekannte Meldung"),
                "link": feed_eintrag.get("link", ""),
            })
    return alle_eintraege


# ---------- Congress ----------


def name_passt_zum_congress_filter(text):
    """Prueft, ob ein Name in der gewuenschten Personen-Liste steht."""
    if len(CONGRESS_NAME_FILTER) == 0:
        return True
    text_klein = text.lower()
    for gesuchter_name in CONGRESS_NAME_FILTER:
        if gesuchter_name.lower() in text_klein:
            return True
    return False


def finde_spalte(spalten, suchbegriff):
    """Findet den Spaltennamen, der den Suchbegriff enthaelt (Gross-/Kleinschreibung egal)."""
    for spalte in spalten:
        if suchbegriff.lower() in str(spalte).lower():
            return spalte
    return None


def extrahiere_name(politiker_text):
    """
    Extrahiert nur den Namen aus dem Politiker-Zellentext.
    Auf capitoltrades.com stehen Name, Partei, Kammer und Bundesstaat
    ohne Leerzeichen aneinandergehaengt in einer Zelle, z.B.
    'Pete SessionsRepublicanHouseTX' -> 'Pete Sessions'.
    """
    treffer = re.match(r"^(.*?)(Republican|Democrat|Independent)", politiker_text)
    if treffer:
        return treffer.group(1).strip()
    return politiker_text.strip()


def extrahiere_ticker(issuer_text):
    """
    Extrahiert das Tickersymbol aus dem 'Traded Issuer'-Zellentext, z.B.
    'Agree Realty CorpADC:US' -> 'ADC'. Falls kein Ticker-Muster
    gefunden wird, wird der rohe Text zurueckgegeben.
    """
    treffer = re.search(r"([A-Z]{1,6}):[A-Z]{2}$", issuer_text)
    if treffer:
        return treffer.group(1)
    return issuer_text.strip()


def wandle_betragstext_in_zahl(text):
    """
    Wandelt einen einzelnen Betragstext wie '15K', '1M' oder '500'
    in eine Zahl um. Gibt None zurueck, wenn das nicht geht.
    """
    treffer = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*([KMkm]?)$", text.strip())
    if not treffer:
        return None
    zahl = float(treffer.group(1).replace(",", "."))
    einheit = treffer.group(2).upper()
    if einheit == "K":
        zahl = zahl * 1000
    elif einheit == "M":
        zahl = zahl * 1000000
    return zahl


def hole_obergrenze_aus_spanne(spannen_text):
    """
    Liest aus einer Congress-Betragsspanne wie '15K - 50K' oder
    '1M - 5M' die OBERGRENZE als Zahl heraus. Gibt None zurueck, wenn
    sich der Text nicht lesen laesst -- dann wird die Meldung bewusst
    durchgelassen statt still verworfen (siehe Modul-Docstring).
    """
    # Alle Zahlen-mit-Einheit-Bausteine im Text einsammeln, unabhaengig
    # davon, ob als Trennzeichen ein Bindestrich, Gedankenstrich oder
    # etwas anderes verwendet wird.
    bausteine = re.findall(r"[0-9]+(?:[.,][0-9]+)?\s*[KMkm]?", spannen_text)
    zahlen = []
    for baustein in bausteine:
        zahl = wandle_betragstext_in_zahl(baustein)
        if zahl is not None:
            zahlen.append(zahl)
    if len(zahlen) == 0:
        return None
    return max(zahlen)


def ist_auf_watchlist(name):
    """Prueft, ob ein Politikername auf der Watchlist steht (Teilstring-Suche)."""
    name_klein = name.lower()
    for watchlist_name in WATCHLIST_NAMEN:
        if watchlist_name.lower() in name_klein:
            return True
    return False


def baue_congress_eintrag(zeile, spalte_politiker, spalte_issuer, spalte_size, spalte_datum, spalte_typ):
    """Wandelt eine Tabellenzeile von capitoltrades.com in Name/Ticker/Betrag um."""
    politiker_text = str(zeile.get(spalte_politiker, "")) if spalte_politiker else "Unbekannt"
    issuer_text = str(zeile.get(spalte_issuer, "")) if spalte_issuer else "?"
    size_text = str(zeile.get(spalte_size, "")).strip() if spalte_size else "?"
    datum_text = str(zeile.get(spalte_datum, "")).strip() if spalte_datum else ""
    typ_text = str(zeile.get(spalte_typ, "")).strip().lower() if spalte_typ else ""

    name = extrahiere_name(politiker_text)
    ticker = extrahiere_ticker(issuer_text)
    betrag = size_text if size_text.lower() != "nan" else "?"

    auf_watchlist = ist_auf_watchlist(name)

    titel = name + ": " + ticker + " (" + betrag + ")"
    if auf_watchlist:
        titel = "TOP-TRADER | " + titel

    # Datum fliesst nur in die ID ein (fuer korrekte Duplikat-Erkennung),
    # nicht in den angezeigten Text.
    eintrag_id = "capitoltrades-" + name + "-" + ticker + "-" + betrag + "-" + datum_text

    eintrag = {"id": eintrag_id, "title": titel, "link": CAPITOL_TRADES_URL}

    if "buy" in typ_text:
        richtungs_tag = "chart_with_upwards_trend"
    elif "sell" in typ_text:
        richtungs_tag = "chart_with_downwards_trend"
    else:
        richtungs_tag = None

    if auf_watchlist:
        # Stern zuerst, damit er in der Benachrichtigung vorne steht
        tags = ["star"]
        if richtungs_tag:
            tags.append(richtungs_tag)
        eintrag["tags"] = tags
        # "max" ist die hoechste ntfy-Stufe und durchbricht auch
        # Nicht-Stoeren-Einstellungen
        eintrag["prioritaet"] = "max"
    else:
        if richtungs_tag:
            eintrag["tags"] = [richtungs_tag]

    obergrenze = hole_obergrenze_aus_spanne(betrag)
    if not auf_watchlist and obergrenze is not None and obergrenze >= GROSSER_BETRAG_SCHWELLE:
        eintrag["prioritaet"] = "high"

    # Diese Zusatzangaben werden nur zum Filtern gebraucht, nicht angezeigt.
    eintrag["congress_typ"] = typ_text
    eintrag["congress_obergrenze"] = obergrenze
    eintrag["auf_watchlist"] = auf_watchlist

    return eintrag


def congress_eintrag_ist_relevant(eintrag):
    """
    Prueft, ob ein Congress-Eintrag gemeldet werden soll: richtige
    Handelsrichtung, gross genug, passender Politikername.
    Watchlist-Personen umgehen Betrags- und Richtungsfilter komplett --
    bei denen willst du jede Bewegung sehen.
    """
    if not name_passt_zum_congress_filter(eintrag["title"]):
        return False

    if eintrag.get("auf_watchlist"):
        return True

    typ_text = eintrag.get("congress_typ", "")
    if NUR_KAEUFE_CONGRESS and "sell" in typ_text:
        return False

    obergrenze = eintrag.get("congress_obergrenze")
    if obergrenze is None:
        print("Betragsspanne nicht lesbar, wird durchgelassen:", eintrag["title"])
        return True
    if obergrenze < MINDESTBETRAG_USD:
        return False

    return True


def hole_congress_eintraege():
    """
    BEST EFFORT: Scraped die neuesten Congress-Trades von
    capitoltrades.com. Siehe Hinweis im Modul-Docstring oben.

    Enthaelt einen Retry mit Wartezeit, weil GitHub-Actions-Runner-IPs
    von Bot-Schutzsystemen haerter behandelt werden als Heim-IPs
    (429 Too Many Requests).
    """
    header = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
        "Referer": "https://www.google.com/",
    }

    anzahl_versuche = 3
    antwort = None
    for versuch in range(1, anzahl_versuche + 1):
        antwort = requests.get(CAPITOL_TRADES_URL, headers=header, timeout=20)
        if antwort.status_code != 429:
            break
        wartezeit = versuch * 15
        print("429 bekommen, warte", wartezeit, "Sekunden und versuche es erneut (Versuch", versuch, "von", anzahl_versuche, ")")
        time.sleep(wartezeit)

    antwort.raise_for_status()

    tabellen = pd.read_html(antwort.text)
    if len(tabellen) == 0:
        print("Keine Tabelle auf capitoltrades.com gefunden -- Seitenstruktur hat sich vermutlich geaendert.")
        return []

    tabelle = tabellen[0]
    spalten = list(tabelle.columns)
    print("Gefundene Spalten in der Congress-Tabelle:", spalten)

    spalte_politiker = finde_spalte(spalten, "politician")
    spalte_issuer = finde_spalte(spalten, "issuer")
    spalte_size = finde_spalte(spalten, "size")
    spalte_datum = finde_spalte(spalten, "published") or finde_spalte(spalten, "traded")
    spalte_typ = finde_spalte(spalten, "type")

    eintraege = []
    for _, zeile in tabelle.iterrows():
        eintrag = baue_congress_eintrag(
            zeile, spalte_politiker, spalte_issuer, spalte_size, spalte_datum, spalte_typ
        )
        eintraege.append(eintrag)
    return eintraege


def filtere_congress_eintrag(eintrag):
    """
    Anreicherungsfunktion fuer Congress: gibt None zurueck, wenn der
    Eintrag nicht gemeldet werden soll. Laeuft nur fuer neue Eintraege.
    """
    if not congress_eintrag_ist_relevant(eintrag):
        return None
    return eintrag


# ---------- Gemeinsame Verarbeitung ----------


def eintrag_passt_zum_filter(titel):
    """Prueft, ob ein Eintrag einen der gewuenschten Ticker enthaelt."""
    if len(TICKER_FILTER) == 0:
        return True
    titel_gross = titel.upper()
    for ticker in TICKER_FILTER:
        if ticker.upper() in titel_gross:
            return True
    return False


def verarbeite_eintraege(eintraege, gesehene_liste, gesehene_set, anreicherungsfunktion=None):
    """
    Geht Eintraege durch, filtert neue heraus und verschickt Benachrichtigungen.
    Falls eine anreicherungsfunktion uebergeben wird, laeuft sie NUR fuer
    tatsaechlich neue Eintraege -- so entstehen teure Zusatz-Requests
    nicht bei jedem Lauf fuer alle Feed-Eintraege. Gibt die
    anreicherungsfunktion None zurueck, wird der Eintrag nicht gemeldet,
    aber trotzdem als gesehen vermerkt.
    """
    for eintrag in eintraege:
        eintrag_id = eintrag.get("id", eintrag.get("link", ""))
        if eintrag_id in gesehene_set:
            continue
        gesehene_set.add(eintrag_id)
        gesehene_liste.append(eintrag_id)

        if anreicherungsfunktion:
            eintrag = anreicherungsfunktion(eintrag)
            if eintrag is None:
                continue

        titel = eintrag.get("title", "Unbekannte Meldung")
        if eintrag_passt_zum_filter(titel):
            sende_benachrichtigung(
                titel,
                tags=eintrag.get("tags"),
                prioritaet=eintrag.get("prioritaet"),
            )
            print("Benachrichtigung verschickt:", titel)


def verarbeite_quelle(name, hole_funktion, gesehene_liste, gesehene_set, fehlerzaehler, anreicherungsfunktion=None):
    """
    Fuehrt eine Quelle aus, faengt Fehler ab und pflegt den
    Fehlerzaehler. Erreicht eine Quelle FEHLER_SCHWELLE Fehlschlaege
    hintereinander, wird EINMAL eine Stoerungsmeldung aufs Handy
    geschickt -- sonst wuerde ein dauerhaft kaputter Teil unbemerkt
    bleiben, weil alle Fehler ja abgefangen werden.
    """
    try:
        eintraege = hole_funktion()
        verarbeite_eintraege(eintraege, gesehene_liste, gesehene_set, anreicherungsfunktion)
        if fehlerzaehler.get(name, 0) > 0:
            print("Quelle", name, "funktioniert wieder.")
        fehlerzaehler[name] = 0
    except Exception as fehler:
        anzahl = fehlerzaehler.get(name, 0) + 1
        fehlerzaehler[name] = anzahl
        print("Fehler bei Quelle", name, "(Fehlschlag Nummer", anzahl, "):", fehler)
        if anzahl == FEHLER_SCHWELLE:
            sende_benachrichtigung(
                "Stoerung: Quelle " + name + " schlaegt seit " + str(anzahl) + " Laeufen fehl",
                tags=["warning"],
                prioritaet="high",
            )


def main():
    gesehene_liste, gesehene_set = lade_gesehene_eintraege()
    fehlerzaehler = lade_fehlerzaehler()

    verarbeite_quelle(
        "SEC", hole_sec_eintraege, gesehene_liste, gesehene_set, fehlerzaehler,
        anreicherungsfunktion=anreichere_sec_eintrag,
    )
    verarbeite_quelle("EU", hole_eu_eintraege, gesehene_liste, gesehene_set, fehlerzaehler)
    verarbeite_quelle(
        "Congress", hole_congress_eintraege, gesehene_liste, gesehene_set, fehlerzaehler,
        anreicherungsfunktion=filtere_congress_eintrag,
    )

    speichere_gesehene_eintraege(gesehene_liste)
    speichere_fehlerzaehler(fehlerzaehler)


if __name__ == "__main__":
    main()
