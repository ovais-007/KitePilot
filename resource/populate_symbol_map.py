import csv
import json
import os
import difflib

# Input and output file paths
RESOURCE_DIR = os.path.dirname(__file__)
CSV_FILE = os.path.join(RESOURCE_DIR, 'EQUITY_L.csv')
JSON_FILE = os.path.join(RESOURCE_DIR, 'symbol_map.json')

symbol_map = {}

with open(CSV_FILE, newline='', encoding='utf-8') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        name = row['NAME OF COMPANY'].strip().upper()
        symbol = row['SYMBOL'].strip().upper()
        # Remove common suffixes for better matching
        for suffix in [" LIMITED", " LTD", ", LTD", ", LIMITED"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        print(f"Looking up: '{name.strip().upper()}' in symbol map")
        symbol_map[name] = symbol

with open(JSON_FILE, 'w', encoding='utf-8') as jsonfile:
    json.dump(symbol_map, jsonfile, indent=2, ensure_ascii=False)

print(f"Wrote {len(symbol_map)} entries to {JSON_FILE}")



# symbol = find_symbol(name, SYMBOL_MAP) 