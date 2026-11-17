# Pokémon Card Collection

Multi-user Pokémon card collection app. Flask + PostgreSQL + Pillow, built on the
[collection app](https://github.com/Dwaynet86/collection-app) (same login, users, per-user collections and roles).

Take a photo of a card and it is identified, matched to the published card record (TCGdex), and added to
your collection with its variant and condition. Browse what you own three ways:

| View | Shows |
|------|-------|
| **Overview** | The page you land on: estimated value (and how many cards are priced), unique cards and copies, expansions collected and completed, Pokémon covered, your most valuable cards, the expansions closest to complete (with a link to the missing cards), most valuable expansions, Pokémon progress by generation, recent additions and a wanted-list summary. Everything comes from data already stored, so it makes no API requests. |
| **Gallery** | Every card you own, shown with its official artwork: quantity, variant, condition, value. Filter by expansion, rarity, variant; sort by name, expansion, value. |
| **Expansions** | Every expansion you collect (tick off "Only expansions I collect" to browse all of them) with its estimated value (for expansions you have cards from) and a progress bar. Open one to see all its cards, yours highlighted and the rest greyed out. Filter All / Collected / Missing. Click a card to open its details; there, **← / →** buttons (or the arrow keys) step to the previous / next card in the expansion. Every tile shows **finish badges**: **N** normal, **H** holofoil, **R** reverse holofoil (also **1st**, **1H**, **P**). A filled badge is a finish you own; a dashed one exists but you don't own it yet, and editors can click it to add a Near Mint copy of that finish. The **Missing …** drop-down lists the cards that come in a finish you don't have (for example, every card missing its reverse holo). The **Master set** switch makes completion count every finish of a card instead of just the card; it applies to the Expansions list, the expansion page and the Overview. The **★ Add all N missing cards to the wanted list** button at the top puts only that expansion's missing cards on the wanted list (and **Clear this expansion's wanted cards** undoes it). On a card you don't have, editors get two buttons: **☆** puts it on the wanted list and **+** adds one copy at once (default finish, Near Mint). Cards TCGdex has no artwork for are looked up on a fallback image source (`ART_FALLBACK_URLS`), and otherwise show a placeholder with the card's name and number; on the card page an editor can **upload an image or paste a link** to use instead, or press **Download again**. Artwork is shared by every collection on the server. |
| **PokeDex** | All 1,025 Pokémon by Pokédex number, yours highlighted. Filter by generation or collected/missing. Open one to see every card featuring it. |
| **Wanted** | Cards you're looking for (☆ on any card you don't have). A card leaves the list when you add a copy. Each collection has a public link that anyone can open with no login: it shows the cards only, with no links into the app. Owners can turn the public link off. |

## Phones

The app is built for phones as well as desktops: a compact header (Members and Maintenance move into your account menu), two cards across on small screens, bigger touch targets, full-width buttons and filters, and a previous / next bar pinned to the bottom of the card page (swipe left or right there too). Add it to your home screen from the browser menu for an app-like launch. To use it from a phone on your network, start the server with `python run.py --host 0.0.0.0` and open `http://<computer's address>:5000`.

## Docker and TrueNAS SCALE

A `Dockerfile`, a GitHub Actions workflow (`.github/workflows/docker.yml`, builds and publishes the image to ghcr.io on every push to `main`) and a ready-to-edit `docker-compose.truenas.yml` are included. The step-by-step guide, including updating by restarting the app, is in [docker/TRUENAS.md](docker/TRUENAS.md). The container applies the database schema itself on every start and answers `/healthz` for health checks.

## How identification works

1. The app finds the card in your photo, straightens it, and shrinks it to a small JPEG (under 100 KB).
   Only that cropped card is sent to the free [TCG Tracking](https://openapi.tcgtracking.com) image-scan
   service. It needs no key. Your original photo stays on your server, and is deleted as soon as you confirm the card (or discard it; leftovers go after an hour). Photos are never kept: the collection shows the published artwork.
2. The scan returns the best-matching products with a percentage. The app looks up each product's name,
   number and set, then **matches** them to TCGdex's published card records (scoring on name, number,
   set size, set code), checking cards it has cached first.
3. You **confirm** the best match (or pick another, or search by name). Only then is the card saved,
   and all stored details come from the published record, never from the photo.

If the scan can't match the photo (or the service is down), you can search by name and number instead.
A weak match is retried upside down and then with the whole photo. Lay the card flat and upright on a
plain background for the best results.

## Where the data comes from, and what gets cached

| Source | Provides | Limits |
|--------|----------|--------|
| [TCGdex](https://tcgdex.dev) | Expansions, every card, rarity, HP, variants, Pokédex numbers, artwork | No key, no published rate limit (it asks bulk users to cache, which this app does) |
| TCGdex (prices) | TCGplayer market price per finish, USD, inside each card record | Same free API as above, no key |
| PokeWallet (optional) | Fallback prices for cards TCGdex has none for | Free plan: 100 requests/hour, 1,000/day |
| TCG Tracking Open API | Identifies the photo (scan) and looks up the matched product | No key. Rate limits and terms aren't published, so be gentle |

Nothing is fetched twice:

- **An expansion's whole card list is one request** (`/sets/<id>`), cached in Postgres.
- **Artwork** comes from TCGdex's static image host, not the API. It is saved to `uploads/catalog/` the first
  time it's shown, so the app serves it from your own disk afterwards.
- **Adding the first card from an expansion downloads the rest of it in the background**: every card's full
  record plus all artwork (about 2 requests per card, 4 at a time). The expansion page shows progress. You can also
  press **Download whole expansion** on any expansion, and **Refresh data** to re-fetch it. Opening an expansion
  you haven't downloaded still works: the card list is fetched once and artwork is saved as you scroll.
- **Prices** come from TCGdex: one request per card, no matching involved. A card is priced when you add it (and re-priced
  if its price is over a day old), when you press **Refresh price** on its page, or for the whole collection with
  **Refresh all prices** on the Maintenance page. PokeWallet is only asked when TCGdex has no price for a card, and
  "Refresh all" tries at most `PRICE_FALLBACK_MAX` (default 15) such cards per run to protect your hourly limit.
  Without a PokeWallet key everything still works; cards TCGdex has no price for just count as $0.

## Pokémon view

Each card is linked to the Pokémon on it using TCGdex's `dexId` (accurate, handles tag teams), once the card's
full record is stored. Until then a name-based guess is used. A Pokémon's page lists the cards the app has
cached: from expansions you've opened or downloaded, and cards you've added.

## Data model

```
users, collections, collection_members     accounts and per-collection roles
wanted_cards                           each collection's wanted list
card_sets, cards, card_species             cached TCGdex data + prices (shared by all users)
species                                    National Pokédex (scripts/build_species.py)
owned_cards                                one row per (collection, card, variant, condition) + quantity
```

Adding the same card, variant and condition again raises the quantity.

Pokémon sprites are loaded by your browser from the PokeAPI sprites repo via jsDelivr.

## Production

```bash
gunicorn -w 3 -b 127.0.0.1:8000 run:app   # use -b 0.0.0.0:8000 to listen on your network
```

Put it behind HTTPS (and set `SESSION_COOKIE_SECURE=1`). The rate-limit counter and login throttle are
per process, so with several workers the "requests left" figure is approximate; PokeWallet enforces
the real limit either way.

## Layout

```
app/
  __init__.py              app factory
  config.py                env-driven settings
  auth.py, permissions.py  login, default-deny, collection roles (from the collection app)
  tcgdex.py                TCGdex client: sets, cards, Pokédex numbers, artwork
  pokewallet.py            PokeWallet client (fallback prices only), rate tracking
  cardnum.py               collector-number helpers
  identify.py              candidate scoring (name, number, set)
  cardcrop.py              find, straighten and shrink the card in a photo (OpenCV)
  tcgtracking.py           TCG Tracking scan client
  photoid.py               photo -> scan -> TCGdex candidates
  catalog_service.py       TCGdex <-> Postgres cache <-> artwork cache, expansion downloads, prices
  catalog_repository.py    sets, cards, card->Pokémon links
  owned_repository.py      what a collection owns; gallery and Pokémon queries
  collection_repository.py, user_repository.py
  species.py               Pokédex and card-name matching (fallback)
  images.py                temporary photo handling (deleted after confirming), artwork cache
  routes/                  auth, admin, members, collections, cards, art
schema.sql                 idempotent schema
scripts/                   init_db, create_user, check_setup, build_species
tests/                     pytest (database stubbed)
```

## Tests

```bash
pytest
```

The tests stub the database, so they check pages, permissions, parsing and matching but not the SQL.
The first run against a real database is the real test of the queries.

## Upgrading an existing install

Run `python scripts/init_db.py` again after updating. It is safe to repeat and adds anything new (the wanted list needs a table and a column; master-set mode adds one more column to `collections`).

## Upgrading from the first version (PokeWallet catalog)

`python scripts/init_db.py` detects the old catalog tables and replaces them, but refuses if any cards are
owned (card ids changed). Remove those cards first, or start from an empty database.

## Troubleshooting

- **Wrong or missing card details**: run `python scripts/check_setup.py` and compare the printed fields with
  `normalize_set`, `normalize_resume` and `normalize_detail` in `app/tcgdex.py`.
- **No price on a card**: TCGdex may not publish one (common for promos and special sets). Press **Refresh price**, or set
  `POKEWALLET_API_KEY` to try PokeWallet as a fallback. `check_setup.py` step 3b shows the raw TCGdex `pricing` block.
- **Value looks low**: the estimate counts a variant with no price as $0, and ignores condition.
- **Expansion download stuck or failed**: the page shows the reason; press **Download whole expansion** again, it
  skips what's already stored. A download left "running" after a crash is retried automatically after 30 minutes.
- **Photos not identified**: lay the card flat and upright on a plain background, then run `python scripts/check_setup.py card-photo.jpg`: it saves `scan-crop.jpg` (what was sent) and prints the scan hits.
- **Phone photos (HEIC)**: supported through `pillow-heif`.
- **Camera button**: only shown on touch devices, and the browser needs HTTPS (or localhost) to open the camera.
