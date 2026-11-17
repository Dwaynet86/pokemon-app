"""Create the database schema and load the Pokedex. Usage: `python scripts/init_db.py`."""
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import species  # noqa: E402
from app.config import Config  # noqa: E402

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema.sql"


def main() -> None:
    with psycopg.connect(Config.DATABASE_URL) as conn:
        conn.execute(SCHEMA_FILE.read_text())
        for dex, name, gen in species.load():
            conn.execute(
                """INSERT INTO species (dex_number, name, generation) VALUES (%s, %s, %s)
                   ON CONFLICT (dex_number) DO UPDATE SET name = EXCLUDED.name, generation = EXCLUDED.generation""",
                (dex, name, gen),
            )
    print(f"Schema applied; {len(species.load())} Pokemon loaded.")


if __name__ == "__main__":
    main()
