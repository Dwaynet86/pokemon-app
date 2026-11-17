"""Rebuild app/data/species.json (the National Pokédex). Only needed to refresh it; the result is committed.

    python scripts/build_species.py

Source: PokeAPI CSV data (https://github.com/PokeAPI/pokeapi), English names.
Writes [[dex_number, name, generation], ...] ordered by dex number.
"""
import csv
import io
import json
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
OUT = Path(__file__).resolve().parent.parent / "app/data/species.json"
ENGLISH = "9"


def fetch(name: str) -> list[dict]:
    with urllib.request.urlopen(BASE + name, timeout=60) as resp:
        return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))


def main() -> None:
    species = {int(r["id"]): int(r["generation_id"]) for r in fetch("pokemon_species.csv")}
    names = {int(r["pokemon_species_id"]): r["name"]
             for r in fetch("pokemon_species_names.csv") if r["local_language_id"] == ENGLISH}
    rows = [[n, names[n], species[n]] for n in sorted(species) if n in names]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(rows)} species")


if __name__ == "__main__":
    main()
