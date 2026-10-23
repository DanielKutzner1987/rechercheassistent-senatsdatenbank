# Rechercheassistent – Senatsdatenbank

PDFs durchsuchen und mit Gemini verständliche Antworten mit Links auf die passenden
PDF-Seiten erhalten. Enthält Open WebUI, Ollama für lokale Embeddings und Qdrant.
Die drei mitgelieferten Demo-PDFs werden beim ersten Start erzeugt; ihre Angaben sind erfunden.

## Starten unter Windows

Du brauchst **Docker Desktop mit Linux-Containern**, Internet und einen eigenen
**Gemini-API-Schlüssel**. Keine lokale GPU und keine Python-Installation nötig.
Als Orientierung: 8 GB freier RAM für Docker und 10 GB freier Speicher.

1. ZIP in einen eigenen Ordner entpacken und Docker Desktop starten.
2. **`start-demo.cmd` doppelklicken**, den Gemini-Schlüssel auf Aufforderung eingeben.
3. Der Browser öffnet sich nach der Einrichtung. Einen Benutzer anlegen und
   **Rechercheassistent - Senatsdatenbank** auswählen.

Beim ersten Start werden die Dienste und das Embedding-Modell heruntergeladen.
Das kann einige Minuten dauern. Danach darf das Konsolenfenster geschlossen werden.
Ein vorhandener Schlüssel wird wiederverwendet. Für einen erneuten Start dieselbe Datei öffnen.

Standardadresse: **http://localhost:3002**. Der Aufbau verwendet eigene Docker-Volumes
und verändert keinen anderen Stack. Falls die Ports 3002 oder 8094 bereits belegt sind,
`WEBUI_PORT` bzw. `API_PORT` in der beim Start erzeugten `.env` ändern.

## Ausprobieren

- „Wie viele Mitarbeiter hat die Musterverkehr AG?“ → 450 insgesamt; 7 nur im Beschwerdemanagement.
- „Wie viele Busse hat die Beispielbus GmbH?“ → Keine Einzelzahl; 120 ist die gemeinsame Flotte.
- „Wie viele Gebäude sind mit Photovoltaik ausgestattet?“ → 12; 20 ist ein Plan für 2027.

Die Antworten sollten die passende Demo-PDF, Seite 1, verlinken. Modellantworten
können trotzdem falsch sein: wichtige Aussagen im Original prüfen.

## Eigene PDFs

1. Die drei Demo-PDFs aus **`data`** entfernen und eigene PDFs dort ablegen.
2. **`PDFs-einlesen.cmd` doppelklicken.** Das aktualisiert die Suche und startet die Oberfläche wieder.

Geänderte und entfernte Dateien werden beim Import berücksichtigt. Es gibt keinen
automatischen Ordnerwächter. Reine Bildscans benötigen vorher Texterkennung.
Der Assistent ist auf Parlamentsdokumente abgestimmt; deren Personen-/Fraktionsstatistiken
sind nicht auf beliebige PDF-Typen übertragbar.

**Stoppen:** `Stoppen.cmd` öffnen. Dateien, Schlüssel, Chats und Daten-Volumes bleiben erhalten.

## Daten und Schlüssel

Die Suche und der Import laufen lokal. Zur Beantwortung werden ausgewählte Dokumenttexte,
Fragen und begrenzter Gesprächskontext an Google gesendet. Themenübersichten senden Titel
und Auszüge. Beim Einrichten/Import wird keine Gemini-Testanfrage ausgelöst.
Es gelten die Quoten und Preise deines API-Projekts; ein Schlüssel garantiert keine kostenlose Nutzung.

Der Schlüssel liegt ausschließlich lokal in `secrets/gemini_api_key.txt`, ist von Git
und Docker-Builds ausgeschlossen und wird im Windows-Setup durch Dateirechte geschützt.
Keine echten PDFs, Schlüssel, Datenbanken oder Chats sind im Download enthalten.
Die Anwendung ist für den eigenen Rechner gedacht, nicht für einen öffentlichen Internetdienst.
[Weitere Hinweise zu Daten und Zugriff](docs/PRIVACY.md).

## Wenn etwas nicht klappt

- **Docker nicht bereit:** Docker Desktop starten und Linux-Container aktivieren, dann erneut starten.
- **Kein Modell sichtbar:** In der WebUI unter Admin → Einstellungen → Verbindungen die
  OpenAI-kompatible Verbindung `http://app:8094/v1` mit Platzhalter `local-demo` prüfen.
  Dort keinen Gemini-Schlüssel eintragen und keine zusätzliche Wissenssammlung an das Modell hängen.
- **Google 429:** Kontingent oder Anfragelimit erreicht. **503:** Anbieter vorübergehend überlastet.
- **Start bleibt hängen:** Im Projektordner `docker compose logs --tail 60 app webui` ausführen.
  Protokolle vor dem Weitergeben auf private Inhalte prüfen.

## Linux/macOS oder manueller Start

`.env.example` nach `.env` kopieren. Schlüssel mit einem lokalen Editor in
`secrets/gemini_api_key.txt` speichern. Unter Linux/macOS: `chmod 700 secrets`
und `chmod 600 secrets/gemini_api_key.txt`. Dann im Projektordner:

```sh
docker compose build
docker compose run --rm demo
docker compose up -d ollama qdrant
docker compose run --rm index
docker compose up -d app webui
```

Danach http://localhost:3002 öffnen. Bei eigenen PDFs den Demo-Schritt auslassen.
Zum Neuimport `docker compose stop app webui`, dann die letzten beiden Schritte wiederholen.
`docker compose stop` beendet den Aufbau. **Kein `down -v`: Das löscht die gespeicherten Daten.**

## Entwicklung

Python 3.11/3.12: `python -m pip install -r requirements.txt`, anschließend
`python -m pip install --no-deps -e .` und `python -m unittest discover -s tests -v`.
Die Tests brauchen keinen API-Schlüssel. Vor dem Teilen: `python scripts/check_release.py`.
Ein kompletter Docker-Erststart auf einem unabhängigen Rechner ist noch nicht bestätigt.

Prototyp, keine garantierte Vollständigkeit oder fachliche Richtigkeit. Quellenprüfung
kontrolliert IDs/Seiten, nicht die Wahrheit einer Aussage. Tabellen können falsch extrahiert werden.

[Lizenz](LICENSE) · [Drittanbieter](THIRD_PARTY.md)
