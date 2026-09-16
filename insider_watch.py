"""
Insider-Trade-Watcher
----------------------
Ueberwacht:
    1) SEC Form-4-Meldungen (US-Firmen-Insider: CEOs, CFOs, Direktoren)
    2) optionale selbst konfigurierte RSS/Atom-Feeds fuer europaeische
       Directors'-Dealings-Meldungen
    3) Trades von US-Kongressmitgliedern, gescraped von
       capitoltrades.com (kostenlos, kein API-Key)

Neue Eintraege werden per ntfy.sh als Push-Benachrichtigung aufs Handy
geschickt, im Format "Name: TICKER (Betrag)". Zusaetzlich kommt einmal
taeglich und einmal woechentlich eine Zusammenfassung.

Abhaengigkeiten (einmalig installieren):
    pip install -r requirements.txt

DIE WICHTIGSTEN SCHRAUBEN ZUM EINSTELLEN (siehe Konfiguration unten):
    QUELLEN_AKTIV              -- nur Kongress? nur SEC? oder alles?
    WATCHLIST_NAMEN            -- Personen, deren Trades IMMER durchkommen
    SMART_FILTER_AKTIV         -- kluge Abwaegung statt starrer Betragsgrenze
    SCORE_SCHWELLE             -- wie streng der Smart-Filter ist
    MINDESTBETRAG_USD          -- harte Untergrenze, darunter nie etwas
    RELEVANTE_TRANSAKTIONSCODES-- welche SEC-Transaktionsarten zaehlen
    NUR_KAEUFE_CONGRESS        -- bei Congress nur Kaeufe statt auch Verkaeufe
    CONGRESS_NAME_FILTER       -- nur bestimmte Politiker
    TICKER_FILTER              -- nur bestimmte Aktien
    ZUSAMMENFASSUNG_STUNDE     -- wann Tages-/Wochenbericht rausgeht

ZWEISTUFIGES FILTERN (wichtig zu verstehen):
    Stufe 1, harte Filter: falsche Formularart, falsche Transaktionsart,
      falscher Politiker, Betrag unter MINDESTBETRAG_USD. Was hier
      rausfliegt, ist komplett weg -- es taucht auch in keiner
      Zusammenfassung auf.
    Stufe 2, Smart-Filter: alles, was Stufe 1 ueberlebt, wird in
      statistik.json MITGESCHRIEBEN. Ob es aber auch einen Push
      ausloest, entscheidet die Punktebewertung (siehe unten).
    Dadurch bleibt der Push selektiv, waehrend die Zusammenfassung
    vollstaendig ist: nichts geht verloren, es klingelt nur seltener.

DER SMART-FILTER (bewerte_trade):
    Die Ausgangsfrage war: was sagt mehr aus -- die ANZAHL der Kaeufe
    oder die SUMME des Gekauften? Antwort: beides, aber verschieden,
    darum werden beide Achsen getrennt bepunktet und addiert.

      Betrag des Einzeltrades      0-40 Punkte
        Ueberzeugung einer einzelnen Person. Logarithmisch skaliert,
        weil der Sprung von 50k auf 500k viel mehr aussagt als der von
        5M auf 5,5M.

      Cluster: verschiedene Kaeufer  0-30 Punkte
        Wenn mehrere unabhaengige Insider innerhalb von
        CLUSTER_FENSTER_TAGE dieselbe Aktie kaufen, ist das
        erfahrungsgemaess das staerkste Einzelsignal ueberhaupt --
        staerker als ein einzelner grosser Kauf. Genau deshalb kommt
        ein Cluster aus drei kleinen Kaeufen durch, ein einsamer
        mittelgrosser Kauf dagegen nicht.

      Gesamtsumme in der Aktie      0-20 Punkte
        Wie viel Geld im Fenster insgesamt in diesen Ticker floss.

      Handelsfrequenz der Person  -10 bis +10 Punkte
        Wer viermal am Tag handelt, sagt mit einem einzelnen Kauf
        wenig. Wer zweimal im Jahr handelt, sagt damit viel. Seltene
        Trader bekommen Punkte, Vielhaendler verlieren welche.

    Ab SCORE_SCHWELLE Punkten gibt es einen Push. Im Log steht bei
    jeder Bewertung die komplette Aufschluesselung, damit sich die
    Schwelle anhand echter Laeufe nachjustieren laesst.

    Bewusste Eigenheit: Der ERSTE Kaeufer eines Clusters kann noch
    keine Cluster-Punkte bekommen, den Cluster gibt es zu dem
    Zeitpunkt ja noch nicht. Erst der zweite und dritte loesen aus --
    und die Zusammenfassung zeigt den Cluster dann vollstaendig.

    Immer durch, egal wie die Punkte stehen:
      - Personen von der WATCHLIST_NAMEN
      - Eintraege ohne lesbaren Betrag (lieber eine zu viel)

    Zu Verkaeufen: Cluster und Gesamtsumme zaehlen nur Kaeufe, ein
    Verkauf kommt also hoechstens auf Betrag plus Seltenheit (max. 50
    Punkte). Schaltest du Verkaeufe frei (RELEVANTE_TRANSAKTIONSCODES
    bzw. NUR_KAEUFE_CONGRESS), kommen daher nur die richtig grossen
    durch. Das ist so gewollt: ein Insider verkauft aus hundert
    Gruenden, er kauft nur aus einem.

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
      der Vergleich ist also eindeutig.
    - Bei Congress gibt es KEINEN exakten Betrag, sondern nur eine
      Spanne wie "15K - 50K" (gesetzlich so vorgesehen). Fuer den
      Vergleich UND fuer die Punktebewertung wird daraus die
      OBERGRENZE genommen, damit ein moeglicherweise grosser Trade
      nicht faelschlich rausfliegt. Beispiel: "50K - 100K" wird als
      100.000 gewertet. Das faerbt die Summen in den Zusammenfassungen
      systematisch nach oben ein -- bewusst so, denn ein zu hoch
      geschaetzter Trade kostet nur eine Meldung, ein zu niedrig
      geschaetzter kostet ein Signal.
    - Laesst sich eine Congress-Spanne nicht lesen (unerwartetes
      Format), wird die Meldung DURCHGELASSEN statt still verworfen,
      und im Log erscheint ein Hinweis.

Wichtig zu EU/AT-Directors'-Dealings:
    Seit 3.7.2016 veroeffentlicht die FMA diese Meldungen NICHT mehr
    selbst -- das macht jeder Emittent einzeln. Es gibt dafuer keinen
    zentralen Feed. Firmen-Feeds koennen unten bei EU_FEEDS eingetragen
    werden, falls vorhanden. EU-Eintraege haben kein maschinenlesbares
    Betragsfeld und kommen darum immer durch.

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

Dateien, die zwischen Laeufen bestehen bleiben muessen:
    seen_entries.json  -- schon gemeldete IDs (gegen Doppel-Pushes)
    error_state.json   -- Fehlschlaege pro Quelle in Folge
    statistik.json     -- Trade-Historie fuer Smart-Filter und Berichte
    Alle drei werden vom GitHub-Actions-Workflow zurueck ins Repo
    committet. Fehlt eine davon im Commit, faengt der zugehoerige
    Zaehler bei jedem Lauf wieder bei null an.

Zeitrechnung:
    Gerechnet und gespeichert wird ueberall in UTC, passend zu
    last_run.txt und zum Actions-Runner.

    EINE Ausnahme: der Zeitpunkt der Berichte haengt an der
    Boersen-Zeitzone (BOERSEN_ZEITZONE), nicht an UTC. Grund ist die
    Sommerzeit -- Handelsschluss der NYSE ist immer 16:00 Ortszeit New
    York, in UTC ist das aber 20:00 im Sommer und 21:00 im Winter. Eine
    fest verdrahtete UTC-Stunde wuerde den Bericht im Winter VOR
    Handelsschluss verschicken. Ueber die Boersen-Zeitzone stimmt es
    ganzjaehrig, und weil Europa und die USA fast gleichzeitig
    umstellen, liegt 16:30 New York praktisch immer auf 22:30
    mitteleuropaeischer Zeit.

Konfiguration ueber Umgebungsvariablen:
    SEC_USER_AGENT und NTFY_TOPIC werden aus Umgebungsvariablen
    gelesen, damit sie nicht im (oeffentlichen) Repo-Code stehen. In
    GitHub Actions kommen sie aus den Repository Secrets.
"""

import os
import json
import time
import re
import math
import requests
import feedparser
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------- Konfiguration ----------

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Timo Beispielname deine-email@example.com"
)

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "timo-insider-watch-x7k2p9")

# ---------- Welche Quellen ueberhaupt laufen ----------
#
# Hier stellst du ein, WER durchkommt:
#   nur Kongressmitglieder      -> "SEC": False, "EU": False, "Congress": True
#   nur Firmen-Insider (SEC)    -> "SEC": True,  "EU": False, "Congress": False
#   alles                       -> ueberall True
#
# Eine abgeschaltete Quelle wird gar nicht erst abgerufen: keine
# Requests, keine Eintraege, keine Bewertung, nichts in den Berichten.
#
# ACHTUNG beim Wiedereinschalten: Eintraege einer abgeschalteten Quelle
# landen nicht in seen_entries.json. Schaltest du sie spaeter wieder
# ein, gilt alles, was dann gerade im Feed steht, als neu -- der erste
# Lauf danach kann also einen Schwung Meldungen auf einmal schicken.
QUELLEN_AKTIV = {
    "SEC": True,
    "EU": True,
    "Congress": True,
}

# Harte Untergrenze. Alles darunter wird komplett verworfen und taucht
# auch in keiner Zusammenfassung auf -- das ist die Rauschgrenze.
# Bewusst niedriger als die alte 100.000er-Grenze, damit der
# Smart-Filter kleine Kaeufe noch sehen und zu einem Cluster
# zusammensetzen kann. Zum Abschalten: auf 0 setzen.
MINDESTBETRAG_USD = 25000

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

# ---------- Smart-Filter ----------

# Auf False setzen, um das alte Verhalten zu bekommen: dann entscheidet
# allein MINDESTBETRAG_USD (den du dann sinnvollerweise wieder auf
# 100000 stellst).
SMART_FILTER_AKTIV = True

# Ab wie vielen Punkten ein Trade einen Push wert ist. Groessenordnung
# zum Gefuehl bekommen (alle Beispiele ohne Watchlist):
#    ~13  einzelner 150k-Kauf von jemandem, der staendig handelt
#    ~40  einzelner 500k-Kauf von jemandem, der selten handelt
#    ~45  drei verschiedene Leute kaufen je 50k derselben Aktie
#    ~54  einzelner 2M-Kauf von jemandem, der selten handelt
# Hoeher = weniger Pushes. Niedriger = mehr. Die Zusammenfassung
# enthaelt so oder so alles, hier stellst du nur ein, wann es klingelt.
SCORE_SCHWELLE = 40

# Ab so vielen Punkten wird mit hoher Prioritaet verschickt (kommt
# auch im Stumm-Modus durch).
HOHE_PRIORITAET_SCORE = 65

# Wie weit zurueck fuer die Cluster-Erkennung geschaut wird. 14 Tage
# passt grob zur Meldepraxis: SEC-Insider muessen binnen 2 Werktagen
# melden, bei Congress dauert es laenger, aber Cluster bilden sich dort
# auch ueber Wochen.
CLUSTER_FENSTER_TAGE = 14

# ---------- Zusammenfassungen ----------

# Zeitzone, in der die Berichtsuhrzeit gemeint ist. Die der US-Boersen,
# damit der Bericht ganzjaehrig nach Handelsschluss kommt und nicht
# zweimal im Jahr durch die Sommerzeitumstellung verrutscht.
BOERSEN_ZEITZONE = "America/New_York"

# Uhrzeit in BOERSEN_ZEITZONE, ab der die Berichte rausgehen. Der erste
# Lauf nach diesem Zeitpunkt schickt sie.
# 16:30 New York = eine halbe Stunde nach Handelsschluss von NYSE und
# Nasdaq (16:00) = 22:30 mitteleuropaeischer Zeit.
ZUSAMMENFASSUNG_STUNDE = 16
ZUSAMMENFASSUNG_MINUTE = 30

# Zeitzone, in der die Uhrzeit IM Bericht angezeigt wird: deine eigene.
# Betrifft nur den angezeigten Text. WANN der Bericht ausgeloest wird,
# haengt allein an BOERSEN_ZEITZONE.
ANZEIGE_ZEITZONE = "Europe/Vienna"

# Wochentag fuer den Wochenbericht: Montag=0 ... Sonntag=6.
# Gemeint ist der Wochentag in BOERSEN_ZEITZONE. Sonntag ist ein
# Ruhetag an der Boerse -- willst du den Wochenbericht lieber direkt
# nach dem letzten Handelstag, trag hier 4 (Freitag) ein.
WOCHENBERICHT_WOCHENTAG = 6

# Wie viele Eintraege pro Rangliste im Bericht stehen.
BERICHT_TOP_ANZAHL = 5

# ---------- Feeds und Quellen ----------

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

# Timeout fuer SEC-Anfragen. Grosszuegig, weil der getcurrent-Feed
# gemessen regelmaessig ueber 12 Sekunden braucht -- mit dem frueheren
# 15-Sekunden-Timeout lief die Quelle immer wieder grundlos in einen
# Fehlschlag.
SEC_TIMEOUT_SEKUNDEN = 30

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

# Nur Meldungen melden, deren Titel einen dieser Ticker enthaelt.
# Leere Liste = alles melden.
TICKER_FILTER = []  # z.B. ["AAPL", "TSLA", "MSFT"]

# ---------- Dateien mit Zustand ----------

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
# Stoerungsmeldung aufs Handy geschickt. Bei einem Lauf alle 15 Minuten
# sind 10 Fehlschlaege rund 2,5 Stunden Ausfall.
FEHLER_SCHWELLE = 10

# Datei mit der Trade-Historie. Basis fuer Cluster-Erkennung,
# Haeufigkeitsbewertung und die Zusammenfassungen.
STATISTIK_FILE = "statistik.json"

# Wie lange Trades aufgehoben werden. Muss laenger sein als
# CLUSTER_FENSTER_TAGE und laenger als eine Woche (Wochenbericht).
STATISTIK_AUFBEWAHRUNG_TAGE = 35

# ---------- Watchlist: besonders beobachtete Personen ----------
#
# Trades dieser Personen werden IMMER gemeldet: sie umgehen den
# Mindestbetrag, den Smart-Filter UND die Nur-Kaeufe-Regel, bekommen
# einen Stern, hoechste Prioritaet (kommt auch im Stumm-Modus durch)
# und ein "TOP-TRADER"-Praefix im Text.
#
# Abgeglichen wird per Teilstring im Namen, also reicht der Nachname.
# Die Liste gilt fuer ALLE Quellen: hier darf also auch der Name eines
# Firmen-Insiders aus den SEC-Meldungen stehen, nicht nur Politiker.
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

# ---------- Zeit-Hilfsfunktionen ----------

ZEITFORMAT = "%Y-%m-%dT%H:%M:%SZ"


def jetzt_utc():
    """Aktueller Zeitpunkt in UTC. Ueberall im Skript wird UTC benutzt."""
    return datetime.now(timezone.utc)


def zeit_als_text(zeitpunkt):
    """Wandelt einen Zeitpunkt in die Textform, die in statistik.json steht."""
    return zeitpunkt.strftime(ZEITFORMAT)


def text_als_zeit(text):
    """
    Liest einen Zeitstempel aus statistik.json zurueck.
    Gibt None zurueck, wenn der Text nicht lesbar ist -- solche
    Eintraege werden beim Aufraeumen behalten statt geloescht, damit
    ein Formatfehler keine Daten wegwirft.
    """
    try:
        return datetime.strptime(text, ZEITFORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


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
        json.dump(fehlerzaehler, datei, indent=1, sort_keys=True)


def lade_statistik():
    """
    Liest die Trade-Historie. Fehlende Felder werden ergaenzt, damit
    eine aeltere oder von Hand bearbeitete Datei nicht alles umwirft.
    """
    statistik = {"trades": [], "letzter_tagesbericht": "", "letzter_wochenbericht": ""}
    if not os.path.exists(STATISTIK_FILE):
        return statistik
    try:
        with open(STATISTIK_FILE, "r", encoding="utf-8") as datei:
            daten = json.load(datei)
    except (ValueError, OSError) as fehler:
        print("statistik.json nicht lesbar, starte mit leerer Historie:", fehler)
        return statistik
    if isinstance(daten, dict):
        statistik.update(daten)
    if not isinstance(statistik.get("trades"), list):
        statistik["trades"] = []
    return statistik


def speichere_statistik(statistik):
    """
    Speichert die Trade-Historie und wirft dabei alles raus, was aelter
    als STATISTIK_AUFBEWAHRUNG_TAGE ist -- sonst waechst die Datei
    unbegrenzt, weil sie bei jedem Lauf ins Repo committet wird.
    """
    grenze = jetzt_utc() - timedelta(days=STATISTIK_AUFBEWAHRUNG_TAGE)
    behalten = []
    for trade in statistik["trades"]:
        zeit = text_als_zeit(trade.get("zeit", ""))
        if zeit is None or zeit >= grenze:
            behalten.append(trade)
    statistik["trades"] = behalten
    with open(STATISTIK_FILE, "w", encoding="utf-8") as datei:
        json.dump(statistik, datei, ensure_ascii=False, indent=1)
    print("Trades in der Historie:", len(behalten))


def notiere_trade(statistik, eintrag):
    """
    Schreibt einen neuen Trade in die Historie -- und zwar JEDEN, der
    die harten Filter ueberlebt hat, unabhaengig davon, ob er gleich
    auch einen Push ausloest. Nur so ist die Zusammenfassung
    vollstaendig und der Cluster-Zaehler kennt auch die kleinen Kaeufe.

    Gibt den gespeicherten Datensatz zurueck, damit der Aufrufer
    hinterher "gemeldet" setzen kann.
    """
    datensatz = {
        "zeit": zeit_als_text(jetzt_utc()),
        "quelle": eintrag.get("quelle", "?"),
        "name": eintrag.get("name", "Unbekannt"),
        "ticker": eintrag.get("ticker", "?"),
        "betrag": eintrag.get("betrag"),
        "richtung": eintrag.get("richtung", "?"),
        "titel": eintrag.get("title", ""),
        "gemeldet": False,
    }
    statistik["trades"].append(datensatz)
    return datensatz


def trades_im_fenster(statistik, tage):
    """Alle Trades der letzten X Tage aus der Historie."""
    grenze = jetzt_utc() - timedelta(days=tage)
    ergebnis = []
    for trade in statistik["trades"]:
        zeit = text_als_zeit(trade.get("zeit", ""))
        if zeit is not None and zeit >= grenze:
            ergebnis.append(trade)
    return ergebnis


# ---------- Benachrichtigung ----------


def sende_benachrichtigung(text, tags=None, prioritaet=None, ueberschrift=None):
    """
    Schickt eine Push-Benachrichtigung ueber ntfy.sh aufs Handy.
    text ist der Nachrichtenkoerper (darf mehrzeilig sein),
    ueberschrift landet in der fetten Titelzeile,
    tags ist eine Liste von ntfy-Tags, prioritaet z.B. "high".

    Die Ueberschrift geht als HTTP-Header raus und bleibt deshalb
    bewusst bei ASCII -- Umlaute in Headern vertragen sich nicht mit
    jedem Client.
    """
    url = "https://ntfy.sh/" + NTFY_TOPIC
    header = {}
    if tags:
        header["Tags"] = ",".join(tags)
    if prioritaet:
        header["Priority"] = prioritaet
    if ueberschrift:
        header["Title"] = ueberschrift
    requests.post(url, data=text.encode("utf-8"), headers=header, timeout=10)


def formatiere_betrag(betrag):
    """Kurze, gut lesbare Betragsangabe fuer Berichte: 1500000 -> '$1.5M'."""
    if betrag is None:
        return "?"
    if betrag >= 1000000000:
        return "${:.1f}Mrd".format(betrag / 1000000000)
    if betrag >= 1000000:
        return "${:.1f}M".format(betrag / 1000000)
    if betrag >= 1000:
        return "${:.0f}K".format(betrag / 1000)
    return "${:.0f}".format(betrag)


def ist_auf_watchlist(name):
    """Prueft, ob ein Name auf der Watchlist steht (Teilstring-Suche)."""
    name_klein = name.lower()
    for watchlist_name in WATCHLIST_NAMEN:
        if watchlist_name.lower() in name_klein:
            return True
    return False


# ---------- SEC ----------


def sec_anfrage(url):
    """
    Fuehrt eine Anfrage an die SEC aus und haelt dabei die von der SEC
    geforderte Ratenbegrenzung ein (max. 10 Anfragen pro Sekunde).
    """
    time.sleep(SEC_PAUSE_SEKUNDEN)
    header = {"User-Agent": SEC_USER_AGENT}
    antwort = requests.get(url, headers=header, timeout=SEC_TIMEOUT_SEKUNDEN)
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


def richtung_aus_sec_code(code):
    """Uebersetzt den SEC-Transaktionscode in 'buy'/'sell'/'sonstige'."""
    if code == "P":
        return "buy"
    if code == "S":
        return "sell"
    return "sonstige"


def anreichere_sec_eintrag(eintrag):
    """
    HARTE Filterstufe fuer SEC. Wird nur fuer NEUE Eintraege aufgerufen
    (siehe verarbeite_eintraege). Gibt None zurueck, wenn der Eintrag
    gar nicht erst erfasst werden soll -- also bei Nicht-Form-4-
    Formularen, nicht gewuenschten Transaktionsarten und Betraegen
    unterhalb der Rauschgrenze.

    Ob es fuer einen Push reicht, entscheidet spaeter der Smart-Filter.
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

    name = details["name"]
    code = details["code"]
    betrag = details["betrag"]
    auf_watchlist = ist_auf_watchlist(name)

    # Watchlist-Personen umgehen beide harten Filter: bei denen willst
    # du jede Bewegung sehen, auch Verkaeufe und auch kleine.
    if not auf_watchlist:
        if len(RELEVANTE_TRANSAKTIONSCODES) > 0 and code not in RELEVANTE_TRANSAKTIONSCODES:
            print("Uebersprungen (Transaktionsart", code, "):", name, details["ticker"])
            return None
        if betrag < MINDESTBETRAG_USD:
            print("Uebersprungen (unter Rauschgrenze: ${:,.0f}):".format(betrag), name, details["ticker"])
            return None

    titel = name + ": " + details["ticker"] + " (" + "${:,.0f}".format(betrag) + ")"
    if auf_watchlist:
        titel = "TOP-TRADER | " + titel

    eintrag["title"] = titel
    eintrag["quelle"] = "SEC"
    eintrag["name"] = name
    eintrag["ticker"] = details["ticker"]
    eintrag["betrag"] = betrag
    eintrag["richtung"] = richtung_aus_sec_code(code)
    eintrag["auf_watchlist"] = auf_watchlist

    if code == "P":
        richtungs_tag = "chart_with_upwards_trend"
    elif code == "S":
        richtungs_tag = "chart_with_downwards_trend"
    else:
        richtungs_tag = None

    if auf_watchlist:
        # Stern zuerst, damit er in der Benachrichtigung vorne steht
        eintrag["tags"] = ["star"] + ([richtungs_tag] if richtungs_tag else [])
        # "max" ist die hoechste ntfy-Stufe und durchbricht auch
        # Nicht-Stoeren-Einstellungen
        eintrag["prioritaet"] = "max"
    elif richtungs_tag:
        eintrag["tags"] = [richtungs_tag]

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
                "quelle": "EU",
                # Kein maschinenlesbarer Betrag: der Smart-Filter laesst
                # solche Eintraege bewusst immer durch.
                "betrag": None,
                "richtung": "?",
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


def richtung_aus_congress_typ(typ_text):
    """Uebersetzt den 'Type'-Text von capitoltrades.com in 'buy'/'sell'/'?'."""
    if "buy" in typ_text:
        return "buy"
    if "sell" in typ_text:
        return "sell"
    return "?"


def baue_congress_eintrag(zeile, spalte_politiker, spalte_issuer, spalte_size, spalte_datum, spalte_typ):
    """Wandelt eine Tabellenzeile von capitoltrades.com in Name/Ticker/Betrag um."""
    politiker_text = str(zeile.get(spalte_politiker, "")) if spalte_politiker else "Unbekannt"
    issuer_text = str(zeile.get(spalte_issuer, "")) if spalte_issuer else "?"
    size_text = str(zeile.get(spalte_size, "")).strip() if spalte_size else "?"
    datum_text = str(zeile.get(spalte_datum, "")).strip() if spalte_datum else ""
    typ_text = str(zeile.get(spalte_typ, "")).strip().lower() if spalte_typ else ""

    name = extrahiere_name(politiker_text)
    ticker = extrahiere_ticker(issuer_text)
    betrag_text = size_text if size_text.lower() != "nan" else "?"

    auf_watchlist = ist_auf_watchlist(name)

    titel = name + ": " + ticker + " (" + betrag_text + ")"
    if auf_watchlist:
        titel = "TOP-TRADER | " + titel

    # Datum fliesst nur in die ID ein (fuer korrekte Duplikat-Erkennung),
    # nicht in den angezeigten Text.
    eintrag_id = "capitoltrades-" + name + "-" + ticker + "-" + betrag_text + "-" + datum_text

    eintrag = {
        "id": eintrag_id,
        "title": titel,
        "link": CAPITOL_TRADES_URL,
        "quelle": "Congress",
        "name": name,
        "ticker": ticker,
        # Aus der Spanne wird die Obergrenze zur Rechengroesse, siehe
        # Modul-Docstring. None heisst "nicht lesbar" -> kommt durch.
        "betrag": hole_obergrenze_aus_spanne(betrag_text),
        "richtung": richtung_aus_congress_typ(typ_text),
        "auf_watchlist": auf_watchlist,
    }

    if eintrag["richtung"] == "buy":
        richtungs_tag = "chart_with_upwards_trend"
    elif eintrag["richtung"] == "sell":
        richtungs_tag = "chart_with_downwards_trend"
    else:
        richtungs_tag = None

    if auf_watchlist:
        # Stern zuerst, damit er in der Benachrichtigung vorne steht
        eintrag["tags"] = ["star"] + ([richtungs_tag] if richtungs_tag else [])
        # "max" ist die hoechste ntfy-Stufe und durchbricht auch
        # Nicht-Stoeren-Einstellungen
        eintrag["prioritaet"] = "max"
    elif richtungs_tag:
        eintrag["tags"] = [richtungs_tag]

    return eintrag


def filtere_congress_eintrag(eintrag):
    """
    HARTE Filterstufe fuer Congress: gibt None zurueck, wenn der
    Eintrag gar nicht erst erfasst werden soll. Laeuft nur fuer neue
    Eintraege. Ob es fuer einen Push reicht, entscheidet spaeter der
    Smart-Filter.

    Watchlist-Personen umgehen Richtungs- und Betragsfilter komplett.
    """
    if not name_passt_zum_congress_filter(eintrag["title"]):
        return None

    if eintrag.get("auf_watchlist"):
        return eintrag

    if NUR_KAEUFE_CONGRESS and eintrag.get("richtung") == "sell":
        return None

    betrag = eintrag.get("betrag")
    if betrag is not None and betrag < MINDESTBETRAG_USD:
        return None

    return eintrag


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


# ---------- Smart-Filter: was ist einen Push wert? ----------


def log_punkte(betrag, unten, oben, max_punkte):
    """
    Rechnet einen Dollarbetrag logarithmisch in Punkte um: bei 'unten'
    gibt es 0 Punkte, bei 'oben' die vollen max_punkte, dazwischen wird
    logarithmisch interpoliert.

    Logarithmisch deshalb, weil Geldbetraege in Groessenordnungen
    denken: der Unterschied zwischen 50k und 500k sagt viel mehr aus
    als der zwischen 5M und 5,45M, obwohl beide 450k auseinanderliegen.
    """
    if betrag is None or betrag <= unten:
        return 0.0
    if betrag >= oben:
        return float(max_punkte)
    anteil = (math.log10(betrag) - math.log10(unten)) / (math.log10(oben) - math.log10(unten))
    return max_punkte * anteil


def sammle_ticker_statistik(statistik, ticker):
    """
    Zaehlt fuer eine Aktie im Cluster-Fenster, wie viele
    VERSCHIEDENE Personen sie gekauft haben und wie viel Geld
    insgesamt hineingeflossen ist.

    Verschiedene Personen, nicht Anzahl Trades: zehn Kaeufe derselben
    Person sind eine Meinung, drei Kaeufe von drei Leuten sind drei.
    """
    if not ticker or ticker == "?":
        return 0, 0.0
    kaeufer = set()
    summe = 0.0
    for trade in trades_im_fenster(statistik, CLUSTER_FENSTER_TAGE):
        if trade.get("ticker") != ticker or trade.get("richtung") != "buy":
            continue
        kaeufer.add(trade.get("name", "?"))
        betrag = trade.get("betrag")
        if betrag:
            summe += betrag
    return len(kaeufer), summe


def zaehle_trades_von(statistik, name):
    """Wie oft diese Person im Cluster-Fenster ueberhaupt gehandelt hat."""
    anzahl = 0
    for trade in trades_im_fenster(statistik, CLUSTER_FENSTER_TAGE):
        if trade.get("name") == name:
            anzahl += 1
    return anzahl


def punkte_fuer_seltenheit(anzahl_trades):
    """
    Bewertet, wie viel ein einzelner Trade dieser Person ueberhaupt
    aussagt. Ro Khanna macht ueber 4.000 Trades im Jahr -- da ist ein
    weiterer Kauf kaum eine Information. Wer zweimal im Jahr handelt
    und dann kauft, meint es ernst.
    """
    if anzahl_trades <= 2:
        return 10.0
    if anzahl_trades <= 5:
        return 5.0
    if anzahl_trades <= 15:
        return 0.0
    if anzahl_trades <= 50:
        return -5.0
    return -10.0


def bewerte_trade(eintrag, statistik):
    """
    Kernstueck des Smart-Filters. Gibt (punkte, teile) zurueck, wobei
    teile die Einzelwerte fuer Log und Meldungstext enthaelt.

    Der Trade muss vorher schon in der Historie stehen (notiere_trade),
    damit er in seinem eigenen Cluster mitgezaehlt wird: ein einsamer
    Kauf ergibt dann 1 Kaeufer und damit 0 Cluster-Punkte.
    Die vier Achsen sind oben im Modul-Docstring erklaert.
    """
    betrag = eintrag.get("betrag")
    ticker = eintrag.get("ticker", "?")
    name = eintrag.get("name", "Unbekannt")

    kaeufer, ticker_summe = sammle_ticker_statistik(statistik, ticker)
    trades_der_person = zaehle_trades_von(statistik, name)

    # 0-40: Ueberzeugung im Einzeltrade. 10k = 0 Punkte, 10M = volle 40.
    betrag_punkte = log_punkte(betrag, 10000, 10000000, 40)

    # 0-30: mehrere unabhaengige Kaeufer derselben Aktie. Zweiter
    # Kaeufer bringt 12, dritter 24, ab dem vierten ist bei 30 Schluss.
    cluster_punkte = min(30.0, max(0, kaeufer - 1) * 12.0)

    # 0-20: wie viel Geld insgesamt in den Ticker floss.
    # 100k = 0 Punkte, 10M = volle 20.
    summen_punkte = log_punkte(ticker_summe, 100000, 10000000, 20)

    # -10 bis +10: sagt ein Trade dieser Person ueberhaupt etwas aus?
    seltenheits_punkte = punkte_fuer_seltenheit(trades_der_person)

    punkte = betrag_punkte + cluster_punkte + summen_punkte + seltenheits_punkte

    teile = {
        "betrag": betrag_punkte,
        "cluster": cluster_punkte,
        "summe": summen_punkte,
        "seltenheit": seltenheits_punkte,
        "kaeufer": kaeufer,
        "ticker_summe": ticker_summe,
        "trades_der_person": trades_der_person,
    }
    return punkte, teile


def eintrag_passt_zum_filter(titel):
    """Prueft, ob ein Eintrag einen der gewuenschten Ticker enthaelt."""
    if len(TICKER_FILTER) == 0:
        return True
    titel_gross = titel.upper()
    for ticker in TICKER_FILTER:
        if ticker.upper() in titel_gross:
            return True
    return False


def pruefe_und_markiere(eintrag, statistik):
    """
    Entscheidet, ob ein bereits erfasster Trade auch einen Push wert
    ist -- und haengt dabei Zusatzinfos an den Eintrag (Cluster-Hinweis
    im Text, hohe Prioritaet bei starkem Signal).
    """
    titel = eintrag.get("title", "")

    if not eintrag_passt_zum_filter(titel):
        return False

    if eintrag.get("auf_watchlist"):
        # Watchlist schlaegt alles: diese Leute willst du immer sehen.
        return True

    if eintrag.get("betrag") is None:
        # Kein lesbarer Betrag (EU-Feeds, unerwartetes Congress-Format):
        # lieber eine Meldung zu viel als eine verpasste.
        print("Kein Betrag lesbar, wird durchgelassen:", titel)
        return True

    if not SMART_FILTER_AKTIV:
        # Smart-Filter aus: die harten Filter haben bereits entschieden.
        return True

    punkte, teile = bewerte_trade(eintrag, statistik)

    print(
        "Bewertung {:5.1f} (Grenze {}) fuer {} | Betrag {:.1f} + Cluster {:.1f}"
        " + Summe {:.1f} + Seltenheit {:+.1f} | {} Kaeufer, {} in {}T,"
        " Person handelte {}x".format(
            punkte, SCORE_SCHWELLE, titel,
            teile["betrag"], teile["cluster"], teile["summe"], teile["seltenheit"],
            teile["kaeufer"], formatiere_betrag(teile["ticker_summe"]),
            CLUSTER_FENSTER_TAGE, teile["trades_der_person"],
        )
    )

    if punkte < SCORE_SCHWELLE:
        return False

    # Wenn ein Cluster der Grund fuer die Meldung ist, gehoert das auch
    # in die Benachrichtigung -- sonst steht da nur ein kleiner Kauf
    # und es ist nicht nachvollziehbar, warum er durchkam.
    if teile["kaeufer"] >= 2:
        eintrag["title"] = (
            titel + " | " + str(teile["kaeufer"]) + " Kaeufer, "
            + formatiere_betrag(teile["ticker_summe"])
            + " in " + str(CLUSTER_FENSTER_TAGE) + "T"
        )

    if punkte >= HOHE_PRIORITAET_SCORE and not eintrag.get("prioritaet"):
        eintrag["prioritaet"] = "high"

    return True


# ---------- Zusammenfassungen ----------


def top_einzeltrades(trades, anzahl):
    """Die groessten Einzeltrades eines Zeitraums, groesster zuerst."""
    mit_betrag = [trade for trade in trades if trade.get("betrag")]
    mit_betrag.sort(key=lambda trade: trade["betrag"], reverse=True)
    return mit_betrag[:anzahl]


def top_ticker(trades, anzahl):
    """
    Die auffaelligsten Aktien eines Zeitraums. Sortiert wird zuerst
    nach der Anzahl VERSCHIEDENER Kaeufer und erst danach nach Summe --
    genau die Gewichtung, die auch der Smart-Filter benutzt.
    """
    pro_ticker = {}
    for trade in trades:
        ticker = trade.get("ticker", "?")
        if not ticker or ticker == "?" or trade.get("richtung") != "buy":
            continue
        eintrag = pro_ticker.setdefault(ticker, {"ticker": ticker, "kaeufer": set(), "summe": 0.0})
        eintrag["kaeufer"].add(trade.get("name", "?"))
        if trade.get("betrag"):
            eintrag["summe"] += trade["betrag"]

    liste = list(pro_ticker.values())
    liste.sort(key=lambda eintrag: (len(eintrag["kaeufer"]), eintrag["summe"]), reverse=True)
    return liste[:anzahl]


def top_trader(trades, anzahl):
    """Wer im Zeitraum am haeufigsten gehandelt hat."""
    pro_name = {}
    for trade in trades:
        name = trade.get("name", "Unbekannt")
        pro_name[name] = pro_name.get(name, 0) + 1
    liste = sorted(pro_name.items(), key=lambda paar: paar[1], reverse=True)
    return liste[:anzahl]


def baue_bericht(trades, zeitraum_text):
    """
    Baut den Textkoerper einer Zusammenfassung. Enthaelt ALLES, was die
    harten Filter passiert hat -- auch das, was keinen Push ausgeloest
    hat. Das ist der Sinn der Sache: streng pushen, vollstaendig
    berichten.
    """
    if len(trades) == 0:
        return zeitraum_text + "\nKeine neuen Trades erfasst."

    gemeldet = len([trade for trade in trades if trade.get("gemeldet")])
    zeilen = [
        zeitraum_text,
        "{} Trades erfasst, davon {} gepusht".format(len(trades), gemeldet),
    ]

    groesste = top_einzeltrades(trades, BERICHT_TOP_ANZAHL)
    if groesste:
        zeilen.append("")
        zeilen.append("Groesste Einzeltrades:")
        for nummer, trade in enumerate(groesste, start=1):
            zeilen.append("{}. {} - {} {}".format(
                nummer, trade.get("name", "?"), trade.get("ticker", "?"),
                formatiere_betrag(trade.get("betrag")),
            ))

    ticker_liste = top_ticker(trades, BERICHT_TOP_ANZAHL)
    if ticker_liste:
        zeilen.append("")
        zeilen.append("Meistgekaufte Aktien:")
        for nummer, eintrag in enumerate(ticker_liste, start=1):
            zeilen.append("{}. {} - {} Kaeufer, {}".format(
                nummer, eintrag["ticker"], len(eintrag["kaeufer"]),
                formatiere_betrag(eintrag["summe"]),
            ))

    trader_liste = top_trader(trades, BERICHT_TOP_ANZAHL)
    if trader_liste:
        zeilen.append("")
        zeilen.append("Aktivste Trader:")
        zeilen.append(", ".join(
            "{} ({}x)".format(name, anzahl) for name, anzahl in trader_liste
        ))

    return "\n".join(zeilen)


def pruefe_zusammenfassungen(statistik):
    """
    Schickt Tages- und Wochenbericht, sobald ein Lauf nach
    ZUSAMMENFASSUNG_STUNDE:ZUSAMMENFASSUNG_MINUTE stattfindet und der
    jeweilige Bericht heute bzw. diese Woche noch nicht raus ist.

    Uhrzeit, Datum und Wochentag werden in BOERSEN_ZEITZONE gerechnet,
    nicht in UTC -- sonst wandert der Bericht mit der Sommerzeit an
    Handelsschluss vorbei (siehe Modul-Docstring). Die Trades selbst
    bleiben unveraendert in UTC gespeichert.

    Gemerkt wird das ueber Datum bzw. ISO-Kalenderwoche in
    statistik.json -- so bekommst du bei einem Lauf alle 15 Minuten
    trotzdem genau einen Bericht pro Tag.

    Der Tagesbericht deckt die letzten 24 Stunden ab, der Wochenbericht
    die letzten 7 Tage. Laeuft der Workflow laengere Zeit gar nicht,
    fehlt die Zeit davor im Bericht -- die Trades selbst bleiben aber
    in statistik.json erhalten.
    """
    jetzt = jetzt_utc().astimezone(ZoneInfo(BOERSEN_ZEITZONE))

    # Als Paar vergleichen, damit auch die Minute zaehlt: um 16:15 ist
    # (16, 15) < (16, 30), um 16:45 nicht mehr.
    if (jetzt.hour, jetzt.minute) < (ZUSAMMENFASSUNG_STUNDE, ZUSAMMENFASSUNG_MINUTE):
        return

    # Ausgeloest wird nach Boersenzeit, angezeigt wird in deiner Zeit.
    anzeige = jetzt.astimezone(ZoneInfo(ANZEIGE_ZEITZONE))

    heute = jetzt.strftime("%Y-%m-%d")
    if statistik.get("letzter_tagesbericht") != heute:
        zeitraum = "Letzte 24 Stunden (Stand {})".format(anzeige.strftime("%d.%m. %H:%M %Z"))
        sende_benachrichtigung(
            baue_bericht(trades_im_fenster(statistik, 1), zeitraum),
            ueberschrift="Tagesbericht " + jetzt.strftime("%d.%m.%Y"),
            tags=["bar_chart"],
            # "low" heisst: wird zugestellt, macht aber keinen Laerm.
            # Fuer einen Abendbericht genau richtig.
            prioritaet="low",
        )
        statistik["letzter_tagesbericht"] = heute
        print("Tagesbericht verschickt.")

    if jetzt.weekday() != WOCHENBERICHT_WOCHENTAG:
        return

    # %G-%V ist die ISO-Kalenderwoche und passt am Jahreswechsel
    # zusammen mit weekday() korrekt, anders als %Y-%W.
    woche = jetzt.strftime("%G-W%V")
    if statistik.get("letzter_wochenbericht") != woche:
        zeitraum = "Letzte 7 Tage (Stand {})".format(anzeige.strftime("%d.%m. %H:%M %Z"))
        sende_benachrichtigung(
            baue_bericht(trades_im_fenster(statistik, 7), zeitraum),
            ueberschrift="Wochenbericht KW " + jetzt.strftime("%V"),
            tags=["calendar"],
        )
        statistik["letzter_wochenbericht"] = woche
        print("Wochenbericht verschickt.")


# ---------- Gemeinsame Verarbeitung ----------


def verarbeite_eintraege(eintraege, zustand, anreicherungsfunktion=None):
    """
    Geht Eintraege durch, filtert neue heraus und verschickt
    Benachrichtigungen.

    Die anreicherungsfunktion ist die HARTE Filterstufe. Sie laeuft NUR
    fuer tatsaechlich neue Eintraege -- so entstehen teure
    Zusatz-Requests nicht bei jedem Lauf fuer alle Feed-Eintraege. Gibt
    sie None zurueck, wird der Eintrag komplett verworfen (aber als
    gesehen vermerkt, damit er nicht erneut geprueft wird).

    Was sie ueberlebt, wandert IMMER in die Statistik. Erst danach
    entscheidet der Smart-Filter, ob es auch klingelt.
    """
    statistik = zustand["statistik"]

    for eintrag in eintraege:
        eintrag_id = eintrag.get("id", eintrag.get("link", ""))
        if eintrag_id in zustand["gesehene_set"]:
            continue
        zustand["gesehene_set"].add(eintrag_id)
        zustand["gesehene_liste"].append(eintrag_id)

        if anreicherungsfunktion:
            eintrag = anreicherungsfunktion(eintrag)
            if eintrag is None:
                continue

        datensatz = notiere_trade(statistik, eintrag)

        if not pruefe_und_markiere(eintrag, statistik):
            continue

        titel = eintrag.get("title", "Unbekannte Meldung")
        sende_benachrichtigung(
            titel,
            tags=eintrag.get("tags"),
            prioritaet=eintrag.get("prioritaet"),
        )
        # Der Titel kann durch den Cluster-Hinweis ergaenzt worden sein.
        datensatz["titel"] = titel
        datensatz["gemeldet"] = True
        print("Benachrichtigung verschickt:", titel)


def verarbeite_quelle(name, hole_funktion, zustand, anreicherungsfunktion=None):
    """
    Fuehrt eine Quelle aus, faengt Fehler ab und pflegt den
    Fehlerzaehler. Erreicht eine Quelle FEHLER_SCHWELLE Fehlschlaege
    hintereinander, wird EINMAL eine Stoerungsmeldung aufs Handy
    geschickt -- sonst wuerde ein dauerhaft kaputter Teil unbemerkt
    bleiben, weil alle Fehler ja abgefangen werden.

    Abgeschaltete Quellen (siehe QUELLEN_AKTIV) werden gar nicht erst
    abgerufen.
    """
    if not QUELLEN_AKTIV.get(name, True):
        print("Quelle", name, "ist abgeschaltet (QUELLEN_AKTIV).")
        return

    fehlerzaehler = zustand["fehlerzaehler"]
    try:
        eintraege = hole_funktion()
        verarbeite_eintraege(eintraege, zustand, anreicherungsfunktion)
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
    zustand = {
        "gesehene_liste": gesehene_liste,
        "gesehene_set": gesehene_set,
        "fehlerzaehler": lade_fehlerzaehler(),
        "statistik": lade_statistik(),
    }

    verarbeite_quelle(
        "SEC", hole_sec_eintraege, zustand,
        anreicherungsfunktion=anreichere_sec_eintrag,
    )
    verarbeite_quelle("EU", hole_eu_eintraege, zustand)
    verarbeite_quelle(
        "Congress", hole_congress_eintraege, zustand,
        anreicherungsfunktion=filtere_congress_eintrag,
    )

    # Eigenes try/except: ein Fehler beim Bericht darf nicht dazu
    # fuehren, dass die frisch gesammelten Trades ungespeichert
    # verloren gehen.
    try:
        pruefe_zusammenfassungen(zustand["statistik"])
    except Exception as fehler:
        print("Zusammenfassung konnte nicht verschickt werden:", fehler)

    speichere_gesehene_eintraege(zustand["gesehene_liste"])
    speichere_fehlerzaehler(zustand["fehlerzaehler"])
    speichere_statistik(zustand["statistik"])


if __name__ == "__main__":
    main()
