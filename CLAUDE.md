# Insider-Watch

Benachrichtigt per Push aufs Handy, wenn legal offengelegte Insider-Aktientransaktionen
veröffentlicht werden. Läuft vollautomatisch in GitHub Actions, unabhängig davon, ob der
PC des Nutzers an ist.

**Wichtig zur Begrifflichkeit:** Es geht ausschließlich um *gesetzlich verpflichtende,
öffentliche* Meldungen (SEC Form 4, STOCK Act), nicht um illegales Insiderhandeln.

## Aufbau

- `insider_watch.py` — das gesamte Programm, eine Datei, keine Module
- `requirements.txt` — requests, feedparser, pandas, lxml, tzdata, **mit festen Versionen**
- `seen_entries.json` — bereits gemeldete Einträge, verhindert Doppelmeldungen
- `error_state.json` — Zähler für aufeinanderfolgende Fehlschläge pro Quelle
- `statistik.json` — Trade-Historie für Smart-Filter und Berichte
- `last_run.txt` — Zeitstempel des letzten Laufs **mit Zustandsänderung**
- `.github/workflows/insider-watch.yml` — der Workflow
- `.github/dependabot.yml` — wöchentliche Update-PRs für pip und Actions

Die Zustandsdateien werden vom Workflow ins Repo zurückcommittet, aber nur wenn sich
`seen_entries.json`, `error_state.json` oder `statistik.json` geändert hat (früher kam durch
`last_run.txt` bei jedem Lauf ein Commit, rund 96 am Tag). Ohne das Zurückcommitten würde bei
jedem Lauf alles als "neu" gelten und eine Benachrichtigungsflut auslösen.

Die Versionen sind festgelegt, weil pandas 3.0 im September 2026 die Congress-Quelle still
kaputtgemacht hat (`read_html` nimmt kein rohes HTML mehr als String an, daher `io.StringIO`).
Updates kommen als Dependabot-PR.

## Datenquellen und ihr tatsächlicher Zustand

| Quelle | Woher | Status |
|---|---|---|
| SEC Form 4 | EDGAR Atom-Feed + Nachladen der Form-4-XML | **Funktioniert.** Name, Ticker und Dollarbetrag werden korrekt extrahiert (verifiziert) |
| Congress | Scraping von capitoltrades.com | **Läuft in GitHub Actions meist nicht.** Siehe unten |
| House | ZIP-Verzeichnis des House Clerk (`{jahr}FD.zip`) | Lokal verifiziert, nur Watchlist, nur "hat gemeldet" + PDF-Link |
| EU / Österreich | selbst eingetragene Firmen-RSS-Feeds | Technisch eingebaut, aber `EU_FEEDS` ist leer |

### SEC

Pro *neuer* Meldung werden zwei Zusatzanfragen gemacht: erst `index.json` der Filing-Directory,
um den XML-Dateinamen zu finden (der variiert je nach Filing-Software, darf nicht geraten werden),
dann die XML-Datei selbst. Das passiert bewusst nur für Einträge, die noch nicht in
`seen_entries.json` stehen — sonst wären es bei jedem Lauf hunderte Anfragen.

Die SEC verlangt einen echten User-Agent mit Kontaktadresse und erlaubt maximal 10 Anfragen
pro Sekunde. Alle SEC-Aufrufe laufen deshalb über `sec_anfrage()`, das eine Pause einlegt.
**Diese Funktion nicht umgehen.**

Der Feed liefert durch Präfix-Matching auch Formulare, die nur mit "4" anfangen
(424B2, 424B5, 425). Das sind Prospekt- und Übernahmemeldungen, keine Insider-Transaktionen.
`ist_form4_eintrag()` filtert sie heraus.

Aus dem Form-4-XML werden außerdem die Rolle (`reportingOwnerRelationship`: isOfficer mit
`officerTitle`, isDirector, isTenPercentOwner) und das Kreuzchen für einen Rule-10b5-1-Plan
(`aff10b5One`, Werte `true`/`false` oder `1`/`0`) gelesen. Sie geben -10 bis +10 Punkte im
Smart-Filter und stehen im Meldungstext. Die Feldnamen sind an einer echten SEC-Meldung
verifiziert, die Auswertung wurde aber nur mit nachgebauten XML-Schnipseln getestet: Die SEC
blockt Anfragen ohne Mailadresse im User-Agent, und die echte Adresse steht nur im Secret.

### House

`disclosures-clerk.house.gov/public_disc/financial-pdfs/{jahr}FD.zip` enthält eine XML mit allen
Offenlegungen des Jahres (Felder: Last, First, FilingType, StateDst, Year, FilingDate, DocID).
FilingType `P` = Periodic Transaction Report. Das PDF liegt unter
`public_disc/ptr-pdfs/{jahr}/{DocID}.pdf`. Die Trades selbst stehen nur im PDF, darum:
nur Watchlist-Personen, kein Eintrag in `statistik.json`, nur Meldungen der letzten
`HOUSE_MAX_ALTER_TAGE` Tage (sonst kommen beim ersten Lauf alle des Jahres). Deckt den Senat
nicht ab. Ob die Seite GitHub-Actions-IPs durchlässt, ist noch nicht beobachtet.

### Congress — das ungelöste Problem

capitoltrades.com liefert an GitHub-Actions-Runner-IPs durchgängig **429 Too Many Requests**,
auch mit Browser-Headern und Retry mit Backoff. Von normalen Heim-IPs funktioniert die Seite.
Vermutlich sind die Actions-IP-Bereiche bei der Seite pauschal gesperrt.

Der Nutzer hat sich bewusst dafür entschieden, das so zu lassen ("ganz auf GitHub Actions
bleiben, Congress-Teil notfalls unzuverlässig lassen") statt auf lokale Ausführung umzustellen
oder für eine API zu bezahlen. **Diese Entscheidung nicht ohne Rückfrage umwerfen.**

Am 16.9.2026 kam die Seite in Actions ausnahmsweise mit 200 durch (daran fiel der pandas-Fehler
auf), danach schlug die Quelle wieder durchgehend fehl. Von einer Heim-IP ist die Extraktion
inzwischen gegen die Live-Seite getestet: Name, Ticker, Spanne und Richtung stimmen.

Als Kennung gegen Doppelmeldungen dient die Nummer der Detailseite `/trades/<nummer>`
(über `read_html(..., extract_links="body")`). **Nicht** die Spalte "Published" verwenden: Die
wandert von `13:01Today` über `13:01Yesterday` zu `15 Sept2026`. Außerdem gibt es inhaltlich
identische Zeilen, die verschiedene Trades sind. Papiere ohne Ticker stehen als `…N/A` in der
Zelle, das `N/A` wird abgeschnitten.

## Backtest (`backtest.py`)

Eigenständiges Skript, läuft **nur lokal** (capitoltrades.com sperrt Actions). Misst für jeden
Congress-Kauf und -Verkauf den Vorsprung gegenüber dem S&P 500 (SPY) nach 30/90/180/365 Tagen,
einmal ab Handelstag und einmal ab dem Handelstag nach der Veröffentlichung. Verkäufe laufen
mit umgekehrtem Vorzeichen. Ergebnisse in `backtest_ergebnisse/`, Zwischenspeicher in
`backtest_daten/` (beides in `.gitignore`).

- **Trades:** Die Seiten `/trades?page=N&pageSize=96` enthalten die Daten als Next.js-JSON
  (`self.__next_f.push`), mit `_txId`, `issuer.issuerTicker` (z.B. `HWM:US`, `BRK/B:US`),
  `txDate`, `pubDate`, `txType`, `value` (Mitte der Spanne), Partei und Kammer. Das ist
  verlässlicher als die HTML-Tabelle, in der Firmenname und Ticker zusammenkleben
  (`Novo Nordisk A/SNVO:US`). Die Seite reicht nur ~3 Jahre zurück (Stand 9/2026: 386 Seiten).
- **Kurse:** Yahoo `query1.finance.yahoo.com/v8/finance/chart/{ticker}`, `adjclose`,
  per `requests` ohne Zusatzpaket. Inoffiziell. Nicht mehr gehandelte Aktien liefern 404 und
  fallen raus (Survivorship Bias, wird in der Ausgabe ausgewiesen).
- Eine Beispielrechnung (NFLX, Byron Donalds, 12.8.2026, 30 Tage) wurde von Hand nachgeprüft.
- **Zwei Fallstricke, die im ersten Lauf zuschlugen:** Der Kurs-Zwischenspeicher muss den
  angeforderten Startzeitpunkt mitspeichern (sonst fallen ältere Trades still aus der Wertung),
  und Yahoo liefert bei kaum gehandelten Papieren Unsinn (`INRE`: 0,0004 $ neben 12,05 $ ergab
  2,8 Mio. % Rendite). Kursreihen mit Tagessprung über Faktor 5 oder unter 100 Handelstagen
  werden deshalb verworfen.
- **Maßstab:** Hauptvergleich ist der gleichgewichtete S&P 500 (`RSP`), nicht `SPY`. Gegen SPY
  gemessen sehen *alle* Käufe schlecht und *alle* Verkäufe gut aus — das ist ein Artefakt der
  Indexgewichtung, keine Aussage über die Personen.
- **Zufallsprüfung:** Mehrere Trades derselben Person in derselben Aktie werden zu einer Wette
  zusammengefasst, daraus kommt ein t-Wert je Person (`wette_je_aktie`). Ergebnis Stand 9/2026:
  8 von 65 Personen auffällig, per Zufall wären 3 bis 4 zu erwarten. Als Gruppe haben
  Kongressmitglieder keinen messbaren Vorsprung.

### Was der Backtest für den Smart-Filter heißt

**Keine der Achsen hält der Prüfung stand.** Nach Gruppen sieht alles plausibel aus (Cluster mit
3+ Käufern +4,8 gegenüber −0,1 bei Einzelkäufern), doch fasst man Trades derselben Aktie
zusammen, bleibt vom Cluster-Effekt +0,02 (t 0,01). Betrag, Seltenheit, Meldeverzug, Kammer und
Depotinhaber liegen alle unter t = 1. Auch die Punktzahl selbst trennt nicht: Die 91 Käufe ab 40
Punkten verteilen sich auf nur 26 Aktien, je Aktie −1,8 (t −0,6).

**Daraus folgt: `SCORE_SCHWELLE` und `MAX_PUSHES_PRO_TAG` sind Mengenregler, keine
Qualitätsfilter.** Die Gewichtung der Achsen wurde deshalb bewusst *nicht* an diesen Daten
nachjustiert — das wäre Anpassung an Rauschen. Gemessene Congress-Pushes pro Woche:
40 Punkte → 0,8; 50 → 0,4; 55 → 0,3; 65 → 0,1. Für die SEC-Meldungen, also die Mehrheit der
Pushes, fehlt eine solche Messung ganz; sie wäre über die EDGAR-Quartalsverzeichnisse möglich.

## Sackgassen — nicht nochmal probieren

Diese Wege wurden geprüft und sind gescheitert. Ohne neuen Anlass nicht wiederholen:

- **Financial Modeling Prep (FMP)** für Congress-Trades: Die Endpunkte `senate-latest` und
  `house-latest` existieren, liefern aber **402 Payment Required** — sie sind nicht im
  kostenlosen Tier enthalten. Der gesamte FMP-Code wurde wieder entfernt.
- **whitehouse.gov** für Trumps Periodic Transaction Reports (OGE Form 278-T): Die
  WordPress-REST-API unter `/wp-json/wp/v2/media` liefert **403 Forbidden**. Die Seite
  erlaubt nur ihren eigenen `whitehouse/v1`-Namespace, der keine Finanzmeldungen enthält.
- **open-cabinet.org** als Alternativquelle für Trump: keine öffentliche API. Die
  Netzwerkanfragen der Seite sind ausschließlich Next.js-interne RSC-Prefetches ohne JSON —
  nicht nutzbar.

**Trump-Tracking gilt damit als nicht automatisierbar.** Es gibt keine bekannte strukturierte
Quelle für Meldungen der Exekutive.

## Infrastruktur

Der eingebaute `schedule`-Trigger wurde **absichtlich entfernt**. GitHub hat geplante Läufe bei
diesem Repo um Stunden verzögert oder verworfen (beobachtet: Lücken von 13:02 bis 18:43).
Stattdessen löst **cron-job.org** alle 15 Minuten per GitHub-API `workflow_dispatch` aus.
Manuell ausgelöste Läufe starten sofort, ohne Warteschlange.

**Den `schedule`-Trigger nicht wieder einbauen** — sonst laufen zwei Auslöser parallel und
kollidieren beim Zurückcommitten der Zustandsdateien.

Konfiguration über GitHub Secrets, nicht im Code (das Repo ist öffentlich):
- `SEC_USER_AGENT` — Name und echte Mailadresse, von der SEC verlangt
- `NTFY_TOPIC` — der ntfy-Topic-Name
- `HEALTHCHECK_URL` — optional, Ping-Adresse eines Totmannschalters (z.B.
  `https://hc-ping.com/<uuid>` von healthchecks.io). Wird am Ende jedes vollständigen Laufs
  aufgerufen. Bleibt der Ping aus, alarmiert der Dienst von außen. Ohne Secret ist das aus.

Benachrichtigungen laufen über ntfy.sh. Wer den Topic-Namen kennt, kann mitlesen und senden —
**der Topic-Name gehört deshalb nicht in eine Datei im Repo.** Antippen einer Meldung öffnet
über den ntfy-Header `Click` die Quelle.

Störungsmeldung: einmal bei `FEHLER_SCHWELLE` Fehlschlägen in Folge, mit Fehlertext. Läuft die
Quelle danach wieder, kommt einmal eine Entwarnung.

## Einstellschrauben

Alle oben im Konfigurationsblock von `insider_watch.py`:

- `WATCHLIST_NAMEN` — Politiker, deren Trades *immer* durchkommen: umgehen Mindestbetrag,
  Nur-Käufe-Filter und Tageslimit, bekommen Stern, `max`-Priorität und ein `TOP-TRADER |`-Präfix.
  Enthält seit dem Backtest nur noch **Pelosi**; Khanna und Gottheimer wurden entfernt, weil
  ihre Bilanz bei großer Datenmenge messbar null ist (Khanna 5.309 Käufe, +0,2, t 0,6).
- `SCORE_SCHWELLE` — aktuell 55 (vorher 40), `HOHE_PRIORITAET_SCORE` 75 (vorher 65)
- `MAX_PUSHES_PRO_TAG` — aktuell 6, harte Obergrenze pro UTC-Tag. Watchlist zählt mit, wird aber
  nie abgewiesen. Zurückgehaltenes steht in `statistik.json` und im Tagesbericht.
- `MINDESTBETRAG_USD` — aktuell 25.000 (harte Rauschgrenze, darüber entscheidet der Smart-Filter mit `SCORE_SCHWELLE`)
- `PUNKTE_SPITZENMANAGER`, `PUNKTE_OFFICER`, `PUNKTE_DIRECTOR`, `PUNKTE_10B5_1_PLAN`, `SPITZEN_TITEL`
- `HOUSE_MAX_ALTER_TAGE` — aktuell 7
- `RELEVANTE_TRANSAKTIONSCODES` — aktuell `["P"]`, also nur offene Marktkäufe
- `NUR_KAEUFE_CONGRESS`
- `CONGRESS_NAME_FILTER`, `TICKER_FILTER`

Beträge bei Congress sind gesetzlich nur Spannen (`15K - 50K`). Für den Vergleich wird die
**Obergrenze** genommen, damit ein möglicherweise großer Trade nicht rausfliegt. Nicht lesbare
Spannen werden **durchgelassen**, nicht still verworfen.

## Offene Punkte

- Der Congress-Teil läuft in GitHub Actions meist nicht (siehe oben). Bewusst so belassen.
- Die Congress-Watchlist-Logik ist in Actions noch nie mit echten Daten durchgelaufen.
- `WATCHLIST_NAMEN` enthält **nur noch** Nancy Pelosi. Ihre Amtszeit endet am
  **3. Januar 2027**, danach taucht sie in keiner Meldung mehr auf — und die Watchlist wäre leer.
  Dann entweder ersetzen oder das Watchlist-Konzept aufgeben. Ein Ersatz sollte aus den
  Backtest-Ergebnissen kommen, nicht aus Presseberichten über „Top-Trader“.
- Die House-Quelle liefert nur für Watchlist-Personen. Schrumpft die Watchlist auf null, ist
  auch diese Quelle still.
- `EU_FEEDS` ist leer. Es gibt seit 2016 keinen zentralen Feed für Directors' Dealings in
  Österreich — jeder Emittent veröffentlicht selbst. Einzelne Firmen-Feeds müssten manuell
  eingetragen werden.

## Konventionen

Vom Nutzer ausdrücklich gewünscht:

- **Deutsche Kommentare und deutsche Variablennamen.**
- **Keine Lambdas.** Code soll so einfach bleiben, dass sich jede Zeile erklären lässt.
- **Vollständige Dateien ausgeben**, keine Diffs oder Schnipsel.
- Keine erfundenen Fakten, Quellen oder Zahlen. Bei Unsicherheit das sagen und nachprüfen,
  bevorzugt in Primärquellen. Der Nutzer legt Wert darauf, dass zwischen Fakt, Schätzung und
  Annahme unterschieden wird.

Weitere Regeln, die sich aus dem Code ergeben:

- Jede Quelle läuft in `verarbeite_quelle()` in ihrem eigenen try/except. Ein Ausfall einer
  Quelle darf die anderen nie mitreißen.
- Teure Zusatzanfragen gehören in die Anreicherungsfunktion, die nur für *neue* Einträge läuft,
  nie in die Abruffunktion.
- Gibt eine Anreicherungsfunktion `None` zurück, wird der Eintrag nicht gemeldet, aber trotzdem
  als gesehen vermerkt — sonst würde er bei jedem Lauf erneut abgefragt.
