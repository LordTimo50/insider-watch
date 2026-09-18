"""
Backtest: Welche Kongressmitglieder haben mit ihren Trades wirklich
besser abgeschnitten als der Markt?
---------------------------------------------------------------------

Laeuft LOKAL auf dem eigenen PC, nicht in GitHub Actions --
capitoltrades.com sperrt die Actions-Server (siehe CLAUDE.md).
Der Watcher (insider_watch.py) wird hiervon nicht beruehrt.

Aufruf:
    python backtest.py                  normaler Lauf, nutzt den Zwischenspeicher
    python backtest.py --neu-trades     Trades neu von capitoltrades.com holen
    python backtest.py --neu-kurse      Kurse neu von Yahoo holen
    python backtest.py --max-seiten 5   nur die ersten 5 Seiten (zum Ausprobieren)
    python backtest.py --horizont 90    Rangliste nach 90 statt 180 Tagen
    python backtest.py --mindest-trades 20

Der erste Lauf dauert laenger (grob 30-60 Minuten, geschaetzt): rund 390
Seiten Trades und ein Kursabruf pro Aktie, jeweils mit Pausen, damit
keine der Seiten ueberlastet wird. Danach liegt alles im
Zwischenspeicher (backtest_daten/) und ein Lauf dauert Sekunden.


WAS GEMESSEN WIRD
    Abgeordnete melden nur, DASS sie gekauft oder verkauft haben --
    nicht, wann und zu welchem Preis sie die Position wieder
    aufgeloest haben. Dazu kommen Betraege nur als Spanne. Der echte
    Gewinn ist deshalb nicht ablesbar und wird angenaehert:

    Fuer jeden Trade wird gemessen, wie sich die Aktie 30, 90, 180
    und 365 Tage danach entwickelt hat, und das mit dem Markt im
    selben Zeitraum verglichen. Die Differenz heisst hier
    "Vorsprung": +5 heisst, die Aktie lief 5 Prozentpunkte besser
    als der Markt. Kurse sind dividendenbereinigt (adjclose), also
    Gesamtrendite.

    Als Markt dienen ZWEI Massstaebe, siehe HAUPT_VERGLEICH weiter
    unten. Kurz: gegen den gleichgewichteten S&P 500 (RSP) gemessen
    zeigt sich, ob die Aktienauswahl gut war; gegen den bekannten
    S&P 500 (SPY), ob ein Indexfonds mehr gebracht haette. Nur gegen
    SPY zu messen ist irrefuehrend.

    Zwei Blickwinkel, beide werden berechnet:
      ab Handelstag       -- wie gut war der Abgeordnete selbst?
                             Einstieg: Schlusskurs am Handelstag
                             (bzw. naechster Handelstag).
      ab Veroeffentlichung -- was haette man durch Nachhandeln
                             verdient? Einstieg: Schlusskurs am
                             Handelstag NACH der Veroeffentlichung,
                             weil man vorher nichts davon wusste.
    Dazwischen liegen bis zu 45 Tage Meldefrist.

    Verkaeufe werden getrennt ausgewertet und mit umgekehrtem
    Vorzeichen: ein Verkauf war "richtig", wenn die Aktie danach
    SCHLECHTER lief als der Markt. Vorsprung +5 bei einem Verkauf
    heisst also: Aktie lief 5 Punkte schlechter als der Markt.

GRENZEN (wichtig beim Lesen der Ergebnisse)
    - Jeder Trade zaehlt gleich viel, egal ob 1.000 oder 5 Mio. Dollar.
      Die Spannen sind zu grob fuer eine saubere Gewichtung.
    - Aktien, die es nicht mehr gibt (Pleite, Uebernahme), liefert
      Yahoo nicht. Sie fallen raus -- das macht die Ergebnisse
      tendenziell ZU GUT. Wie viele Trades das betrifft, steht in der
      Auswertung.
    - Anleihen, Fonds ohne US-Ticker und Optionen ohne Ticker werden
      nicht bewertet.
    - Wer wenige Trades hat, kann einfach Glueck gehabt haben. Die
      Rangliste zeigt darum nur Personen ab --mindest-trades
      bewertbaren Kaeufen, der Rest steht separat in der CSV.
    - Mehrere Zeilen fuer denselben Kauf (z.B. Ehepartner und
      gemeinsames Depot) zaehlen einzeln.
    - Yahoo ist keine offizielle Schnittstelle und kann sich aendern.

DATENQUELLE capitoltrades.com
    Die Trade-Seiten enthalten die Daten zusaetzlich als JSON
    (Next.js-Datenstrom in self.__next_f.push). Das ist deutlich
    verlaesslicher als die HTML-Tabelle: Ticker, Datum im ISO-Format,
    Partei und Kammer stehen dort sauber getrennt. Die Seite reicht
    nur rund 3 Jahre zurueck (Stand September 2026: 386 Seiten zu
    je 96 Trades, fruehester Handelstag 18.9.2023).

ERGEBNISSE landen in backtest_ergebnisse/:
    trades.csv               jeder Trade mit allen Renditen
    politiker_kaeufe.csv     Auswertung pro Person, nur Kaeufe
    politiker_verkaeufe.csv  Auswertung pro Person, nur Verkaeufe
    zusammenfassung.txt      dieselbe Ausgabe wie in der Konsole
"""

import os
import re
import sys
import json
import math
import time
import shutil
import argparse
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd

# ---------- Einstellungen ----------

# Nach so vielen Tagen wird gemessen.
HORIZONTE_TAGE = [30, 90, 180, 365]

# Nach diesem Horizont wird die Rangliste sortiert, wenn nichts
# anderes angegeben ist. 180 Tage: lang genug, dass Zufall weniger
# ins Gewicht faellt, kurz genug, dass die meisten Trades schon
# messbar sind.
STANDARD_HORIZONT = 180

# Mindestanzahl bewertbarer Trades fuer die Rangliste.
STANDARD_MINDEST_TRADES = 10

# Wie viele Jahre zurueck (gemessen am Handelstag).
JAHRE_ZURUECK = 3

# Zwei Vergleichsmassstaebe, beide werden berechnet:
#
#   RSP = S&P 500 GLEICHGEWICHTET, jede der 500 Aktien zaehlt gleich
#         viel. Das ist der faire Massstab fuer "war die Aktienauswahl
#         gut?", denn auch der Abgeordnete kauft eine einzelne Aktie.
#   SPY = S&P 500 nach Boersenwert gewichtet, der bekannte Index. Das
#         ist der Massstab fuer "haette ich mit dem Indexfonds mehr
#         gehabt?".
#
# Der Unterschied ist gross: 2023-2026 trugen wenige riesige
# Technologiewerte den SPY, eine durchschnittliche Einzelaktie blieb
# dahinter zurueck. Misst man nur gegen SPY, sehen ALLE Kaeufe
# schlecht und ALLE Verkaeufe gut aus -- das ist dann keine Aussage
# ueber die Person, sondern ueber den Massstab.
HAUPT_VERGLEICH = "RSP"
NEBEN_VERGLEICH = "SPY"
VERGLEICHE = [("rsp", HAUPT_VERGLEICH), ("spy", NEBEN_VERGLEICH)]

# Wie viele Personen oben und unten in der Konsole stehen.
RANGLISTE_ANZAHL = 15

# Ab welchem t-Wert ein Ergebnis als auffaellig gilt. 2 entspricht
# grob der ueblichen 5-Prozent-Schwelle: Bei reinem Zufall erreicht
# etwa jede zwanzigste Person diesen Wert. Bei 60 bis 80 verglichenen
# Personen sind also 3 bis 4 "Auffaellige" der Normalfall und noch
# kein Hinweis auf Koennen -- darum steht unter jeder Rangliste, wie
# viele Zufall erwarten liesse.
AUFFAELLIG_AB_T = 2.0

# Weniger als so viele verschiedene Aktien: kein t-Wert. Aus zwei
# Wetten laesst sich keine Streuung schaetzen.
MIND_AKTIEN_FUER_T = 3

CAPITOL_TRADES_SEITE = "https://www.capitoltrades.com/trades?page={seite}&pageSize=96"
YAHOO_KURSE = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={von}&period2={bis}&interval=1d"

# Browser-Kennung: capitoltrades.com und Yahoo liefern ohne sie oft nichts.
BROWSER_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Pausen zwischen Anfragen, damit keine Seite ueberlastet wird.
PAUSE_TRADES_SEKUNDEN = 2.0
PAUSE_KURSE_SEKUNDEN = 0.5

# Sicherheitsgrenze, falls die Seite nie "keine Ergebnisse" meldet.
MAX_SEITEN = 1000

# Plausibilitaetsgrenzen fuer Kursreihen. Yahoo liefert bei kaum
# gehandelten Papieren gelegentlich Unsinn: Bei INRE stand ein Kurs
# von 0,0004 Dollar neben 12,05 -- das ergab 2,8 Millionen Prozent
# "Rendite" und hat im ersten Lauf den Durchschnitt aller Kaeufe
# zerstoert (+735 statt +1 Punkt). Reihen mit einem Tagessprung ueber
# diesen Faktor oder mit zu wenigen Handelstagen werden darum
# komplett verworfen, statt einzelne Werte zurechtzubiegen.
MAX_TAGESSPRUNG_FAKTOR = 5
MIND_HANDELSTAGE = 100

# Nach so vielen Tagen gilt der Zwischenspeicher als veraltet.
TRADES_CACHE_TAGE = 7
KURSE_CACHE_TAGE = 7

DATEN_ORDNER = "backtest_daten"
TRADES_ORDNER = os.path.join(DATEN_ORDNER, "trades")
KURSE_ORDNER = os.path.join(DATEN_ORDNER, "kurse")
TRADES_META_DATEI = os.path.join(TRADES_ORDNER, "abruf.json")
ERGEBNIS_ORDNER = "backtest_ergebnisse"

# Findet die Datenstuecke von Next.js im HTML. Jedes Stueck ist ein
# JSON-String-Literal, darum wird es spaeter mit json.loads entpackt.
NEXT_DATEN_MUSTER = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')

# ---------- Ausgabe (Konsole und zusammenfassung.txt gleichzeitig) ----------

AUSGABE_ZEILEN = []


def ausgabe(text=""):
    """Schreibt eine Zeile in die Konsole und merkt sie fuer die Zusammenfassung."""
    print(text)
    AUSGABE_ZEILEN.append(text)


# ---------- Zwischenspeicher ----------


def datei_alter_tage(pfad):
    """Wie viele Tage die Datei alt ist. Fehlt sie, gilt sie als unendlich alt."""
    if not os.path.exists(pfad):
        return float("inf")
    sekunden = time.time() - os.path.getmtime(pfad)
    return sekunden / 86400


def lies_json(pfad):
    with open(pfad, "r", encoding="utf-8") as datei:
        return json.load(datei)


def schreib_json(pfad, daten):
    with open(pfad, "w", encoding="utf-8") as datei:
        json.dump(daten, datei, ensure_ascii=False)


# ---------- Trades von capitoltrades.com ----------


def hole_seite_roh(seite):
    """
    Laedt eine Seite und gibt die Trades als Liste von Dicts zurueck.
    Leere Liste = Ende erreicht. Wirft einen Fehler, wenn die Seite
    Daten zeigt, das JSON aber nicht gefunden wird (Seitenumbau).
    """
    url = CAPITOL_TRADES_SEITE.format(seite=seite)
    antwort = None
    for versuch in range(1, 4):
        antwort = requests.get(url, headers=BROWSER_HEADER, timeout=30)
        if antwort.status_code != 429:
            break
        wartezeit = versuch * 30
        print("  429 bekommen, warte", wartezeit, "Sekunden ...")
        time.sleep(wartezeit)
    antwort.raise_for_status()

    stuecke = NEXT_DATEN_MUSTER.findall(antwort.text)
    text = ""
    for stueck in stuecke:
        text = text + json.loads(stueck)

    position = text.find('"data":[{"_issuerId"')
    if position < 0:
        if "No results" in antwort.text:
            return []
        raise ValueError(
            "Seite " + str(seite) + ": keine Trade-Daten im JSON gefunden, "
            "die Seitenstruktur hat sich vermutlich geaendert."
        )

    # raw_decode liest genau ein JSON-Objekt ab der Position und
    # ignoriert den Rest dahinter. +7 springt ueber '"data":'.
    daten, _ = json.JSONDecoder().raw_decode(text, position + len('"data":'))
    return daten


def ticker_fuer_yahoo(capitol_ticker):
    """
    Wandelt einen Ticker von capitoltrades.com in die Yahoo-Schreibweise.
    'HWM:US' -> 'HWM', 'BRK/B:US' -> 'BRK-B'. Nicht-US-Papiere und
    fehlende Ticker ergeben None, sie werden nicht bewertet.
    """
    if not capitol_ticker or not capitol_ticker.endswith(":US"):
        return None
    ticker = capitol_ticker[:-3]
    ticker = ticker.replace("/", "-").replace(".", "-")
    if not re.match(r"^[A-Z0-9-]{1,10}$", ticker):
        return None
    return ticker


def vereinfache_trade(roh):
    """Nimmt aus dem JSON eines Trades nur, was fuer den Backtest gebraucht wird."""
    politiker = roh.get("politician") or {}
    emittent = roh.get("issuer") or {}

    vorname = politiker.get("nickname") or politiker.get("firstName") or ""
    name = (vorname + " " + (politiker.get("lastName") or "")).strip()

    return {
        "trade_id": roh.get("_txId"),
        "politiker_id": roh.get("_politicianId"),
        "name": name,
        "partei": politiker.get("party") or "",
        "kammer": roh.get("chamber") or politiker.get("chamber") or "",
        "bundesstaat": (politiker.get("_stateId") or "").upper(),
        "firma": emittent.get("issuerName") or "",
        "ticker_capitol": emittent.get("issuerTicker") or "",
        "ticker": ticker_fuer_yahoo(emittent.get("issuerTicker")),
        "sektor": emittent.get("sector") or "",
        "art": roh.get("txType") or "",
        "handelstag": roh.get("txDate") or "",
        # Nur das Datum; die Uhrzeit ist fuer Tageskurse egal.
        "veroeffentlicht": (roh.get("pubDate") or "")[:10],
        "meldeverzug_tage": roh.get("reportingGap"),
        "besitzer": roh.get("owner") or "",
        # Schaetzwert von capitoltrades.com (Mitte der Spanne).
        "wert_geschaetzt": roh.get("value"),
        "preis_gemeldet": roh.get("price"),
    }


def lade_alle_trades(neu_holen, max_seiten):
    """
    Holt alle Trades, Seite fuer Seite, und speichert jede Seite
    einzeln. Bricht der Abruf ab, geht es beim naechsten Start an der
    Stelle weiter. Nach TRADES_CACHE_TAGE (oder mit --neu-trades)
    wird alles neu geholt, weil neue Meldungen die Seiten verschieben.
    """
    zu_alt = datei_alter_tage(TRADES_META_DATEI) > TRADES_CACHE_TAGE
    if (neu_holen or zu_alt) and os.path.exists(TRADES_ORDNER):
        shutil.rmtree(TRADES_ORDNER)
    os.makedirs(TRADES_ORDNER, exist_ok=True)
    if not os.path.exists(TRADES_META_DATEI):
        schreib_json(TRADES_META_DATEI, {"begonnen": datetime.now(timezone.utc).isoformat()})

    alle = []
    for seite in range(1, min(max_seiten, MAX_SEITEN) + 1):
        pfad = os.path.join(TRADES_ORDNER, "seite_{:04d}.json".format(seite))
        if os.path.exists(pfad):
            daten = lies_json(pfad)
        else:
            print("Trades: Seite", seite, "...")
            time.sleep(PAUSE_TRADES_SEKUNDEN)
            daten = hole_seite_roh(seite)
            schreib_json(pfad, daten)
        if len(daten) == 0:
            break
        for roh in daten:
            alle.append(vereinfache_trade(roh))

    # Verschieben sich Seiten waehrend des Abrufs, taucht ein Trade
    # zweimal auf. Die Trade-Nummer ist eindeutig.
    gesehen = set()
    eindeutig = []
    for trade in alle:
        if trade["trade_id"] in gesehen:
            continue
        gesehen.add(trade["trade_id"])
        eindeutig.append(trade)
    return eindeutig


# ---------- Kurse von Yahoo ----------


def hole_kurse(ticker, von_datum, neu_holen):
    """
    Liefert die Tageskurse einer Aktie als zwei parallele Listen
    (Datum als 'JJJJ-MM-TT', dividendenbereinigter Schlusskurs).
    Gibt ([], []) zurueck, wenn Yahoo die Aktie nicht kennt -- das
    wird ebenfalls zwischengespeichert, damit nicht jeder Lauf erneut
    fragt.
    """
    os.makedirs(KURSE_ORDNER, exist_ok=True)
    pfad = os.path.join(KURSE_ORDNER, ticker + ".json")
    if not neu_holen and datei_alter_tage(pfad) <= KURSE_CACHE_TAGE:
        gespeichert = lies_json(pfad)
        # Der gespeicherte Zeitraum muss weit genug zurueckreichen.
        # Sonst faellt jeder aeltere Trade still aus der Wertung --
        # genau das ist beim ersten Lauf mit SPY passiert, weil ein
        # kurzer Probelauf die Datei mit wenigen Monaten angelegt hatte.
        if gespeichert.get("von", "9999") <= von_datum:
            return gespeichert["daten"], gespeichert["kurse"]

    von = int(datetime.strptime(von_datum, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    bis = int(time.time())
    url = YAHOO_KURSE.format(ticker=ticker, von=von, bis=bis)

    time.sleep(PAUSE_KURSE_SEKUNDEN)
    antwort = None
    for versuch in range(1, 4):
        # Verbindungsabbrueche (z.B. SSLEOFError) kommen bei Yahoo
        # vereinzelt vor und sind meist beim naechsten Versuch weg.
        # Beim letzten Versuch wird der Fehler durchgereicht.
        try:
            antwort = requests.get(url, headers=BROWSER_HEADER, timeout=30)
        except requests.exceptions.RequestException as fehler:
            if versuch == 3:
                raise
            print("  Yahoo: Verbindungsfehler bei", ticker, "- neuer Versuch in", versuch * 5, "Sekunden:", fehler)
            time.sleep(versuch * 5)
            continue
        if antwort.status_code != 429:
            break
        wartezeit = versuch * 30
        print("  Yahoo: 429 bekommen, warte", wartezeit, "Sekunden ...")
        time.sleep(wartezeit)

    daten = []
    kurse = []
    if antwort.status_code == 404:
        # Unbekannt oder nicht mehr gehandelt.
        pass
    else:
        antwort.raise_for_status()
        ergebnis = (antwort.json().get("chart") or {}).get("result") or []
        if ergebnis:
            zeitpunkte = ergebnis[0].get("timestamp") or []
            indikatoren = ergebnis[0].get("indicators") or {}
            bereinigt = indikatoren.get("adjclose") or [{}]
            werte = bereinigt[0].get("adjclose") or []
            for zeitpunkt, wert in zip(zeitpunkte, werte):
                # Yahoo liefert an einzelnen Tagen None -- die fehlen dann.
                if wert is None or wert <= 0:
                    continue
                tag = datetime.fromtimestamp(zeitpunkt, timezone.utc).strftime("%Y-%m-%d")
                daten.append(tag)
                kurse.append(wert)

    schreib_json(pfad, {"von": von_datum, "daten": daten, "kurse": kurse})
    return daten, kurse


# ---------- Renditen ----------


def kursreihe_brauchbar(kurse):
    """
    Prueft eine Kursreihe auf offensichtlichen Unsinn. Gibt None
    zurueck, wenn sie brauchbar ist, sonst den Grund als Text.
    """
    if len(kurse) < MIND_HANDELSTAGE:
        return "Kursdaten zu duenn"
    for stelle in range(1, len(kurse)):
        vorher = kurse[stelle - 1]
        jetzt = kurse[stelle]
        sprung = max(jetzt / vorher, vorher / jetzt)
        if sprung > MAX_TAGESSPRUNG_FAKTOR:
            return "Kursdaten unplausibel"
    return None


def kurs_ab(daten, kurse, tag, strikt_danach):
    """
    Erster Kurs am oder nach 'tag' (bzw. strikt danach). Gibt
    (datum, kurs) zurueck oder (None, None), wenn es keinen gibt.
    Die Datumsstrings im Format JJJJ-MM-TT lassen sich direkt
    vergleichen, darum funktioniert die Binaersuche auf ihnen.
    """
    if strikt_danach:
        stelle = bisect_right(daten, tag)
    else:
        stelle = bisect_left(daten, tag)
    if stelle >= len(daten):
        return None, None
    return daten[stelle], kurse[stelle]


def tag_plus(tag, tage):
    datum = datetime.strptime(tag, "%Y-%m-%d") + timedelta(days=tage)
    return datum.strftime("%Y-%m-%d")


def rendite(daten, kurse, einstieg_tag, horizont, strikt_danach):
    """
    Rendite in Prozent vom Einstieg bis 'horizont' Kalendertage danach.
    None, wenn der Zeitraum noch nicht vorbei ist oder Kurse fehlen.
    Gibt ausserdem den tatsaechlichen Einstiegstag zurueck, damit der
    Vergleichsmassstab exakt denselben Zeitraum bekommt.
    """
    start_tag, start_kurs = kurs_ab(daten, kurse, einstieg_tag, strikt_danach)
    if start_tag is None:
        return None, None
    ende_tag, ende_kurs = kurs_ab(daten, kurse, tag_plus(start_tag, horizont), False)
    if ende_tag is None:
        return None, start_tag
    # Liegt zwischen Ziel und gefundenem Kurs mehr als eine Woche, hat
    # die Aktie laenger nicht gehandelt -- dann lieber nicht werten.
    if ende_tag > tag_plus(start_tag, horizont + 7):
        return None, start_tag
    return (ende_kurs / start_kurs - 1) * 100, start_tag


def bewerte_trades(trades, neu_kurse):
    """Rechnet fuer jeden Trade alle Renditen und Vorspruenge aus."""
    fruehester = None
    for trade in trades:
        if trade["handelstag"] and (fruehester is None or trade["handelstag"] < fruehester):
            fruehester = trade["handelstag"]
    if fruehester is None:
        return
    # Etwas Puffer vor dem fruehesten Handelstag.
    von_datum = tag_plus(fruehester, -10)

    vergleichskurse = {}
    for kuerzel, ticker in VERGLEICHE:
        daten, kurse = hole_kurse(ticker, von_datum, neu_kurse)
        if len(daten) == 0:
            raise RuntimeError("Keine Kurse fuer " + ticker + " -- ohne Vergleich kein Backtest.")
        vergleichskurse[kuerzel] = (daten, kurse)

    alle_ticker = []
    for trade in trades:
        if trade["ticker"] and trade["ticker"] not in alle_ticker:
            alle_ticker.append(trade["ticker"])

    kurse_pro_ticker = {}
    # Ticker, deren Abruf an einem Fehler scheiterte -- getrennt von
    # "Yahoo kennt die Aktie nicht", weil ein erneuter Lauf sie
    # nachholt (sie werden nicht zwischengespeichert).
    fehlgeschlagen = set()
    for nummer, ticker in enumerate(alle_ticker, start=1):
        if nummer % 50 == 0 or nummer == len(alle_ticker):
            print("Kurse:", nummer, "von", len(alle_ticker))
        try:
            kurse_pro_ticker[ticker] = hole_kurse(ticker, von_datum, neu_kurse)
        except Exception as fehler:
            print("  Kurse fuer", ticker, "nicht ladbar:", fehler)
            kurse_pro_ticker[ticker] = ([], [])
            fehlgeschlagen.add(ticker)

    # Kursreihen mit offensichtlichem Unsinn aussortieren, bevor sie
    # die Durchschnitte verziehen.
    unbrauchbar = {}
    for ticker in alle_ticker:
        daten, kurse = kurse_pro_ticker.get(ticker, ([], []))
        if len(daten) == 0 or ticker in fehlgeschlagen:
            continue
        grund = kursreihe_brauchbar(kurse)
        if grund is not None:
            unbrauchbar[ticker] = grund
    if unbrauchbar:
        print()
        print(len(unbrauchbar), "Kursreihe(n) aussortiert:")
        for ticker in sorted(unbrauchbar):
            print("  ", ticker, "-", unbrauchbar[ticker])

    if fehlgeschlagen:
        print()
        print(len(fehlgeschlagen), "Aktie(n) nicht ladbar:", ", ".join(sorted(fehlgeschlagen)))
        print("Einfach nochmal starten -- es werden nur diese nachgeholt.")

    for trade in trades:
        # Alle Spalten erst leer anlegen, damit die Tabelle sie auch dann
        # hat, wenn kein einziger Trade bewertbar ist.
        for kuerzel in ["handel", "veroeff"]:
            for horizont in HORIZONTE_TAGE:
                trade["rendite_" + kuerzel + "_" + str(horizont)] = None
                for massstab, _ in VERGLEICHE:
                    trade["vorsprung_" + massstab + "_" + kuerzel + "_" + str(horizont)] = None

        if not trade["ticker"]:
            trade["status"] = "kein US-Ticker"
            continue
        daten, kurse = kurse_pro_ticker.get(trade["ticker"], ([], []))
        if trade["ticker"] in fehlgeschlagen:
            trade["status"] = "Abruf fehlgeschlagen"
            continue
        if len(daten) == 0:
            trade["status"] = "keine Kursdaten"
            continue
        if trade["ticker"] in unbrauchbar:
            trade["status"] = unbrauchbar[trade["ticker"]]
            continue
        trade["status"] = "bewertet"

        # Beim Verkauf zaehlt es als richtig, wenn die Aktie danach
        # schlechter lief als der Markt -> Vorzeichen umdrehen.
        vorzeichen = -1 if trade["art"] == "sell" else 1

        blickwinkel = [
            ("handel", trade["handelstag"], False),
            ("veroeff", trade["veroeffentlicht"], True),
        ]
        for kuerzel, einstieg_tag, strikt_danach in blickwinkel:
            for horizont in HORIZONTE_TAGE:
                spalte = kuerzel + "_" + str(horizont)
                if not einstieg_tag:
                    continue
                aktie, start_tag = rendite(daten, kurse, einstieg_tag, horizont, strikt_danach)
                if aktie is None:
                    continue
                trade["rendite_" + spalte] = aktie
                for massstab, _ in VERGLEICHE:
                    vergleich_daten, vergleich_kurse = vergleichskurse[massstab]
                    # Markt ab exakt demselben Einstiegstag messen.
                    markt, _ = rendite(vergleich_daten, vergleich_kurse, start_tag, horizont, False)
                    if markt is None:
                        continue
                    trade["vorsprung_" + massstab + "_" + spalte] = vorzeichen * (aktie - markt)


# ---------- Auswertung ----------


def wette_je_aktie(gruppe, spalte):
    """
    Fasst mehrere Trades derselben Person in derselben Aktie zu EINER
    Wette zusammen und prueft, ob der Vorsprung mehr als Zufall ist.

    Ohne dieses Zusammenfassen zaehlt dieselbe Entscheidung mehrfach:
    Wer eine Aktie in 40 Tranchen kauft, haette sonst 40 angeblich
    unabhaengige Treffer. Genau das blaeht bei Vielhaendlern die
    Aussagekraft kuenstlich auf.

    Gibt (anzahl_aktien, durchschnitt_je_aktie, t_wert) zurueck.
    Der t-Wert ist None, wenn er sich nicht sinnvoll berechnen laesst.
    """
    werte = gruppe[["ticker", spalte]].dropna()
    if len(werte) == 0:
        return 0, None, None
    je_aktie = werte.groupby("ticker")[spalte].mean()
    anzahl = len(je_aktie)
    schnitt = je_aktie.mean()
    if anzahl < MIND_AKTIEN_FUER_T:
        return anzahl, schnitt, None
    streuung = je_aktie.std()
    if streuung is None or pd.isna(streuung) or streuung == 0:
        return anzahl, schnitt, None
    return anzahl, schnitt, schnitt / (streuung / math.sqrt(anzahl))


def auswertung_pro_person(tabelle):
    """
    Fasst die Trades pro Person zusammen: Anzahl, Durchschnitt und
    Median des Vorsprungs, Trefferquote -- je Blickwinkel und Horizont.
    """
    zeilen = []
    for politiker_id, gruppe in tabelle.groupby("politiker_id"):
        erste = gruppe.iloc[0]
        zeile = {
            "name": erste["name"],
            "partei": erste["partei"],
            "kammer": erste["kammer"],
            "bundesstaat": erste["bundesstaat"],
            "trades_gesamt": len(gruppe),
            "trades_bewertet": int((gruppe["status"] == "bewertet").sum()),
        }
        for massstab, _ in VERGLEICHE:
            for kuerzel in ["handel", "veroeff"]:
                for horizont in HORIZONTE_TAGE:
                    basis = massstab + "_" + kuerzel + "_" + str(horizont)
                    werte = gruppe["vorsprung_" + basis].dropna()
                    zeile["anzahl_" + basis] = len(werte)
                    if len(werte) > 0:
                        zeile["schnitt_" + basis] = werte.mean()
                        zeile["median_" + basis] = werte.median()
                        zeile["treffer_" + basis] = (werte > 0).mean() * 100
                    else:
                        zeile["schnitt_" + basis] = None
                        zeile["median_" + basis] = None
                        zeile["treffer_" + basis] = None

        # Zufallspruefung nur fuer den Hauptmassstab -- sie soll die
        # Rangliste einordnen, nicht jede Spalte verdoppeln.
        for kuerzel in ["handel", "veroeff"]:
            for horizont in HORIZONTE_TAGE:
                basis = "rsp_" + kuerzel + "_" + str(horizont)
                aktien, schnitt, t_wert = wette_je_aktie(gruppe, "vorsprung_" + basis)
                zeile["aktien_" + basis] = aktien
                zeile["schnitt_je_aktie_" + basis] = schnitt
                zeile["t_" + basis] = t_wert
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def zahl(wert, nachkomma=1, vorzeichen=True):
    """Formatiert eine Zahl fuer die Tabelle; leere Werte werden zu '-'."""
    if wert is None or pd.isna(wert):
        return "-"
    if vorzeichen:
        return ("{:+." + str(nachkomma) + "f}").format(wert)
    return ("{:." + str(nachkomma) + "f}").format(wert)


def drucke_rangliste(personen, horizont, mindest, ueberschrift, anzahl):
    """
    Gibt die besten und schlechtesten Personen aus, sortiert nach
    durchschnittlichem Vorsprung ab Handelstag.
    """
    basis = "rsp_handel_" + str(horizont)
    auswahl = personen[personen["anzahl_" + basis] >= mindest]
    auswahl = auswahl.sort_values("schnitt_" + basis, ascending=False)

    ausgabe()
    ausgabe(ueberschrift)
    ausgabe("(nur Personen mit mindestens {} bewertbaren Trades nach {} Tagen: {} Personen)".format(
        mindest, horizont, len(auswahl)))
    if len(auswahl) == 0:
        return

    auffaellige = auswahl[auswahl["t_" + basis].abs() > AUFFAELLIG_AB_T]
    ausgabe("Davon auffaellig (|t| ueber {:.0f}): {} -- bei reinem Zufall waeren {:.0f} bis {:.0f} zu erwarten.".format(
        AUFFAELLIG_AB_T, len(auffaellige), len(auswahl) * 0.04, len(auswahl) * 0.06))

    kopf = "{:<24} {:<3} {:>6} {:>7} {:>10} {:>9} {:>8} {:>7} {:>11} {:>8}".format(
        "Name", "P", "Trades", "Aktien", "Vorsprung", "Median", "Treffer", "t", "Nachhandeln", "vs SPY")
    ausgabe(kopf)
    ausgabe("-" * len(kopf))

    teile = [("Beste", auswahl.head(anzahl))]
    if len(auswahl) > anzahl:
        # Die schlechtesten von unten nach oben, schlechtester zuletzt.
        teile.append(("Schlechteste", auswahl.tail(min(anzahl, len(auswahl) - anzahl))))

    for titel, teil in teile:
        ausgabe(titel + ":")
        for _, zeile in teil.iterrows():
            # Stern markiert: mehr als Zufallsniveau (siehe AUFFAELLIG_AB_T).
            t_wert = zeile["t_" + basis]
            if t_wert is not None and not pd.isna(t_wert) and abs(t_wert) > AUFFAELLIG_AB_T:
                t_text = zahl(t_wert) + "*"
            else:
                t_text = zahl(t_wert)
            ausgabe("{:<24} {:<3} {:>6} {:>7} {:>10} {:>9} {:>7}% {:>7} {:>11} {:>8}".format(
                str(zeile["name"])[:24],
                str(zeile["partei"])[:1].upper(),
                int(zeile["anzahl_" + basis]),
                int(zeile["aktien_" + basis]),
                zahl(zeile["schnitt_" + basis]),
                zahl(zeile["median_" + basis]),
                zahl(zeile["treffer_" + basis], 0, False),
                t_text,
                zahl(zeile["schnitt_rsp_veroeff_" + str(horizont)]),
                zahl(zeile["schnitt_spy_handel_" + str(horizont)]),
            ))


def drucke_gesamtbild(tabelle, art_text):
    """Durchschnitt ueber ALLE Trades einer Art, je Horizont und Blickwinkel."""
    ausgabe()
    ausgabe("Alle " + art_text + " zusammen (Vorsprung in Prozentpunkten):")
    ausgabe("{:>9} {:>8} {:>13} {:>9} {:>8} {:>16} {:>13}".format(
        "Horizont", "Anzahl", "vs " + HAUPT_VERGLEICH, "Median", "Treffer",
        "vs " + HAUPT_VERGLEICH + " nachgeh.", "vs " + NEBEN_VERGLEICH))
    for horizont in HORIZONTE_TAGE:
        handel = tabelle["vorsprung_rsp_handel_" + str(horizont)].dropna()
        veroeff = tabelle["vorsprung_rsp_veroeff_" + str(horizont)].dropna()
        spy = tabelle["vorsprung_spy_handel_" + str(horizont)].dropna()
        if len(handel) == 0:
            continue
        ausgabe("{:>8}T {:>8} {:>13} {:>9} {:>7}% {:>16} {:>13}".format(
            horizont, len(handel), zahl(handel.mean()), zahl(handel.median()),
            zahl((handel > 0).mean() * 100, 0, False),
            zahl(veroeff.mean()) if len(veroeff) > 0 else "-",
            zahl(spy.mean()) if len(spy) > 0 else "-",
        ))


def main():
    parser = argparse.ArgumentParser(description="Backtest der Congress-Trades gegen den S&P 500")
    parser.add_argument("--neu-trades", action="store_true", help="Trades neu holen")
    parser.add_argument("--neu-kurse", action="store_true", help="Kurse neu holen")
    parser.add_argument("--max-seiten", type=int, default=MAX_SEITEN, help="nur so viele Seiten laden")
    parser.add_argument("--horizont", type=int, default=STANDARD_HORIZONT, choices=HORIZONTE_TAGE,
                        help="Horizont in Tagen fuer die Rangliste")
    parser.add_argument("--mindest-trades", type=int, default=STANDARD_MINDEST_TRADES,
                        help="Mindestanzahl bewertbarer Trades fuer die Rangliste")
    argumente = parser.parse_args()

    trades = lade_alle_trades(argumente.neu_trades, argumente.max_seiten)
    grenze = (datetime.now(timezone.utc) - timedelta(days=365 * JAHRE_ZURUECK)).strftime("%Y-%m-%d")
    trades_im_zeitraum = []
    for trade in trades:
        if trade["handelstag"] >= grenze and trade["art"] in ["buy", "sell"]:
            trades_im_zeitraum.append(trade)
    print("Trades geladen:", len(trades), "| davon Kaeufe/Verkaeufe im Zeitraum:", len(trades_im_zeitraum))

    bewerte_trades(trades_im_zeitraum, argumente.neu_kurse)

    tabelle = pd.DataFrame(trades_im_zeitraum)
    os.makedirs(ERGEBNIS_ORDNER, exist_ok=True)
    tabelle.to_csv(os.path.join(ERGEBNIS_ORDNER, "trades.csv"), index=False, encoding="utf-8-sig")

    kaeufe = tabelle[tabelle["art"] == "buy"]
    verkaeufe = tabelle[tabelle["art"] == "sell"]

    ausgabe("=" * 78)
    ausgabe("BACKTEST CONGRESS-TRADES  (Stand {})".format(datetime.now().strftime("%d.%m.%Y %H:%M")))
    ausgabe("=" * 78)
    ausgabe("Zeitraum Handelstage: {} bis {}".format(tabelle["handelstag"].min(), tabelle["handelstag"].max()))
    ausgabe("Kaeufe: {}   Verkaeufe: {}".format(len(kaeufe), len(verkaeufe)))
    ausgabe()
    ausgabe("Bewertbarkeit (Kaeufe und Verkaeufe):")
    for status, anzahl in tabelle["status"].value_counts().items():
        ausgabe("  {:<18} {:>7}  ({:.0f} %)".format(status, anzahl, anzahl / len(tabelle) * 100))
    ausgabe("  'keine Kursdaten' sind vor allem Aktien, die es nicht mehr gibt.")
    ausgabe("  Sie fehlen in der Wertung -- das macht die Ergebnisse eher zu gut.")
    if (tabelle["status"] == "Abruf fehlgeschlagen").any():
        ausgabe("  'Abruf fehlgeschlagen' holt ein erneuter Start nach.")

    drucke_gesamtbild(kaeufe, "Kaeufe")
    drucke_gesamtbild(verkaeufe, "Verkaeufe (Vorsprung = Aktie lief SCHLECHTER als der Markt)")

    personen_kaeufe = auswertung_pro_person(kaeufe)
    personen_verkaeufe = auswertung_pro_person(verkaeufe)
    personen_kaeufe.to_csv(os.path.join(ERGEBNIS_ORDNER, "politiker_kaeufe.csv"), index=False, encoding="utf-8-sig")
    personen_verkaeufe.to_csv(os.path.join(ERGEBNIS_ORDNER, "politiker_verkaeufe.csv"), index=False, encoding="utf-8-sig")

    ausgabe()
    ausgabe("Massstab ist der GLEICHGEWICHTETE S&P 500 (" + HAUPT_VERGLEICH + "), in dem jede Aktie gleich zaehlt.")
    ausgabe("Das ist der faire Vergleich fuer eine einzelne Aktienauswahl. Die letzte Spalte zeigt")
    ausgabe("denselben Wert gegen den bekannten " + NEBEN_VERGLEICH + ", den wenige grosse Technologiewerte tragen.")
    ausgabe("Spalten: P = Partei | Vorsprung = Durchschnitt ab Handelstag | Median = mittlerer Wert,")
    ausgabe("unempfindlich gegen einzelne Ausreisser | Treffer = Anteil der Trades besser als der Markt |")
    ausgabe("Nachhandeln = Durchschnitt, wenn man erst nach der Veroeffentlichung eingestiegen waere |")
    ausgabe("Aktien = verschiedene Aktien, denn 40 Kaeufe derselben Aktie sind EINE Wette, nicht 40 |")
    ausgabe("t = Zufallspruefung auf dieser Basis, Stern ab |t| > {:.0f}. Faustregel: unter 2 ist das".format(AUFFAELLIG_AB_T))
    ausgabe("Ergebnis mit Zufall gut vereinbar, egal wie gross der Vorsprung aussieht.")

    drucke_rangliste(
        personen_kaeufe, argumente.horizont, argumente.mindest_trades,
        "KAEUFE: Vorsprung nach {} Tagen".format(argumente.horizont), RANGLISTE_ANZAHL,
    )
    drucke_rangliste(
        personen_verkaeufe, argumente.horizont, argumente.mindest_trades,
        "VERKAEUFE: Vorsprung nach {} Tagen (positiv = Aktie fiel danach relativ zum Markt)".format(
            argumente.horizont),
        RANGLISTE_ANZAHL,
    )

    ausgabe()
    ausgabe("Hinweis: Das sind Durchschnitte ueber vergangene Trades, keine Prognose. Einzelne")
    ausgabe("Riesengewinne koennen den Durchschnitt stark verziehen -- dann Median beachten.")
    ausgabe("Details pro Trade: " + os.path.join(ERGEBNIS_ORDNER, "trades.csv"))

    with open(os.path.join(ERGEBNIS_ORDNER, "zusammenfassung.txt"), "w", encoding="utf-8") as datei:
        datei.write("\n".join(AUSGABE_ZEILEN) + "\n")


if __name__ == "__main__":
    # Windows-Konsolen koennen sonst an Sonderzeichen in Firmennamen scheitern.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    main()
