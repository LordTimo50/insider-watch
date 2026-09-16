# Insider-Watch

Benachrichtigt per Push aufs Handy, wenn legal offengelegte Insider-Aktientransaktionen
veröffentlicht werden. Läuft vollautomatisch in GitHub Actions, unabhängig davon, ob der
PC des Nutzers an ist.

**Wichtig zur Begrifflichkeit:** Es geht ausschließlich um *gesetzlich verpflichtende,
öffentliche* Meldungen (SEC Form 4, STOCK Act), nicht um illegales Insiderhandeln.

## Aufbau

- `insider_watch.py` — das gesamte Programm, eine Datei, keine Module
- `requirements.txt` — requests, feedparser, pandas, lxml
- `seen_entries.json` — bereits gemeldete Einträge, verhindert Doppelmeldungen
- `error_state.json` — Zähler für aufeinanderfolgende Fehlschläge pro Quelle
- `last_run.txt` — Zeitstempel des letzten Laufs
- `.github/workflows/insider-watch.yml` — der Workflow

Beide Zustandsdateien werden vom Workflow nach jedem Lauf ins Repo zurückcommittet.
Ohne das würde bei jedem Lauf alles als "neu" gelten und eine Benachrichtigungsflut auslösen.

## Datenquellen und ihr tatsächlicher Zustand

| Quelle | Woher | Status |
|---|---|---|
| SEC Form 4 | EDGAR Atom-Feed + Nachladen der Form-4-XML | **Funktioniert.** Name, Ticker und Dollarbetrag werden korrekt extrahiert (verifiziert) |
| Congress | Scraping von capitoltrades.com | **Läuft in GitHub Actions nicht.** Siehe unten |
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

### Congress — das ungelöste Problem

capitoltrades.com liefert an GitHub-Actions-Runner-IPs durchgängig **429 Too Many Requests**,
auch mit Browser-Headern und Retry mit Backoff. Von normalen Heim-IPs funktioniert die Seite.
Vermutlich sind die Actions-IP-Bereiche bei der Seite pauschal gesperrt.

Der Nutzer hat sich bewusst dafür entschieden, das so zu lassen ("ganz auf GitHub Actions
bleiben, Congress-Teil notfalls unzuverlässig lassen") statt auf lokale Ausführung umzustellen
oder für eine API zu bezahlen. **Diese Entscheidung nicht ohne Rückfrage umwerfen.**

Folge: Der gesamte Congress-Teil inklusive Watchlist ist noch nie mit echten Daten durchgelaufen.
Die Extraktion von Name, Ticker und Betrag aus der HTML-Tabelle sowie die Watchlist-Logik sind
**ungetestet**. Falls der Teil irgendwann durchkommt, ist mit Nachbesserungsbedarf zu rechnen.

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

Benachrichtigungen laufen über ntfy.sh. Wer den Topic-Namen kennt, kann mitlesen und senden —
**der Topic-Name gehört deshalb nicht in eine Datei im Repo.**

## Einstellschrauben

Alle oben im Konfigurationsblock von `insider_watch.py`:

- `WATCHLIST_NAMEN` — Politiker, deren Trades *immer* durchkommen: umgehen Mindestbetrag und
  Nur-Käufe-Filter, bekommen Stern, `max`-Priorität und ein `TOP-TRADER |`-Präfix
- `MINDESTBETRAG_USD` — aktuell 100.000
- `RELEVANTE_TRANSAKTIONSCODES` — aktuell `["P"]`, also nur offene Marktkäufe
- `NUR_KAEUFE_CONGRESS`
- `CONGRESS_NAME_FILTER`, `TICKER_FILTER`

Beträge bei Congress sind gesetzlich nur Spannen (`15K - 50K`). Für den Vergleich wird die
**Obergrenze** genommen, damit ein möglicherweise großer Trade nicht rausfliegt. Nicht lesbare
Spannen werden **durchgelassen**, nicht still verworfen.

## Offene Punkte

- Der Congress-Teil läuft in GitHub Actions nicht (siehe oben). Bewusst so belassen.
- Watchlist und Congress-Extraktion sind ungetestet, weil die Quelle nie durchkam.
- `WATCHLIST_NAMEN` enthält Nancy Pelosi. Ihre Amtszeit endet am **3. Januar 2027**, danach
  taucht sie in keiner Meldung mehr auf. Die Liste sollte dann angepasst werden.
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
