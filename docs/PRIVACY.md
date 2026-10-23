# Datenfluss und Sicherheit

Der Aufbau ist **hybrid**, nicht vollständig lokal. PDF-Originale bleiben im eigenen
Ordner. Ausgewählte extrahierte Dokumenttexte, Fragen und ein begrenzter Verlauf gehen
zur Antwortgenerierung an Google. Themenübersichten übertragen einen Katalog aus
Dokumenttiteln und Auszügen. Lokale Parteizählungen brauchen keine Cloud-Anfrage.

Nur Material verwenden, das im eigenen API-Projekt verarbeitet werden darf. Die geltenden
Google-Bedingungen unterscheiden sich nach Produkt/Tarif; maßgeblich sind die aktuellen
[Gemini-Bedingungen](https://ai.google.dev/gemini-api/terms) und die eigenen Projekteinstellungen.
Keine verbindliche Datenschutzbewertung durch dieses Repository.

## Lokale Daten

- SQLite speichert extrahierte Texte, Quellen, Fragen, Modellantworten, Suchereignisse und
  bei erfolgreicher API-Antwort gemeldeten Tokenverbrauch. Auch Fehlversuche hinterlassen Auditdaten.
- Open WebUI speichert Benutzer und Chats in einem getrennten Volume.
- Qdrant speichert Embeddings und Quellenmetadaten; Ollama hält lokale Modellgewichte.
- Auditdaten sind nicht automatisch anonymisiert oder zeitlich begrenzt gelöscht.
- Logs, Volumes und Datenbanken nicht in Fehlerberichte oder Git-Releases übernehmen.

## Schlüssel

`secrets/gemini_api_key.txt` ist eine lokale Klartextdatei. Compose mountet diese Datei
als Secret; dadurch wird sie nicht zu einem verschlüsselten Secret-Manager. Windows-Setup
beschränkt die Ordner-DACL auf den aktuellen Benutzer und SYSTEM. Bei bestehendem Secret
werden Inhalt und Zugriffsrechte nicht automatisch geändert. Dateirechte selbst prüfen.
Der Schlüssel wird nicht im Docker-Image, Compose-Environment, Auditbericht oder Git gespeichert.
Verdächtige Schlüssel, die doch veröffentlicht wurden, beim Anbieter widerrufen.

## Zugriff

App und WebUI werden nur an localhost veröffentlicht. Qdrant und Ollama sind nur im
Compose-Netzwerk erreichbar. Der API-Dienst hat selbst keine Authentifizierung: jeder lokale
Prozess mit Zugriff kann Quellen abrufen und potentiell API-Nutzung auslösen. Auch jeder
autorisierte WebUI-Benutzer kann denselben Dokumentbestand durchsuchen. Keine Mandantentrennung,
Dateirechte pro Benutzer, TLS-Terminierung, Kostenobergrenze oder produktionsreife Ratenbegrenzung.

Nicht ohne zusätzliche Zugriffskontrolle als öffentlichen Dienst betreiben. Ein vorhandenes
WebUI-Login ersetzt keine Absicherung eines separat veröffentlichten API-Ports.

## Veröffentlichung

`check_release.py` kontrolliert ausschließlich getrackte Textdateien auf bekannte Muster
und verbotene Laufzeitdateien. Es öffnet keine ignorierte Schlüsseldatei. Mustererkennung
kann Geheimnisse übersehen. Vor einem Upload `git diff --cached` bzw. die getrackten Dateien
prüfen. Öffentlich verteilte Unterlagen brauchen zudem eine passende Weitergabeberechtigung.
