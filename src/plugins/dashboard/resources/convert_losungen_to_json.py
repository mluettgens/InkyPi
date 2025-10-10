import csv
import json

# Pfade zu den Quelldateien
input_path = "src/plugins/dashboard/resources/Losungen Free 2025.txt"
output_path = "src/plugins/dashboard/resources/losungen.json"

# Einlesen und Umwandeln

import re

def italicize(text):
    # Ersetze /kursiv/ durch <i>kursiv</i>, auch mehrfach pro Feld
    if not isinstance(text, str):
        return text
    return re.sub(r"/(.*?)/", r"<i>\1</i>", text)

with open(input_path, encoding="utf-8") as tsvfile:
    reader = csv.DictReader(tsvfile, delimiter="\t")
    data = []
    for row in reader:
        # Felder entfernen
        row.pop("Wtag", None)
        row.pop("Sonntag", None)
        # Alle Werte auf Kursivsetzung prüfen
        new_row = {k: italicize(v) for k, v in row.items()}
        data.append(new_row)

with open(output_path, "w", encoding="utf-8") as jsonfile:
    json.dump(data, jsonfile, ensure_ascii=False, indent=2)

print(f"Konvertierung abgeschlossen: {len(data)} Einträge in {output_path}")
