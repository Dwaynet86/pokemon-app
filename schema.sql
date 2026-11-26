-- Pokemon card collection schema. Idempotent: safe to re-run.

-- ---- Accounts, collections, membership (same model as the collection app) ---
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT        NOT NULL CHECK (length(btrim(username)) > 0),
    password_hash TEXT        NOT NULL,
    is_admin      BOOLEAN     NOT NULL DEFAULT false,
    is_active     BOOLEAN     NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_key ON users (lower(username));
ALTER TABLE users ADD COLUMN IF NOT EXISTS theme TEXT;   -- page colours, JSON (see app/theme.py)

CREATE TABLE IF NOT EXISTS collections (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL CHECK (length(btrim(name)) > 0),
    slug       TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS collection_members (
    collection_id BIGINT NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL REFERENCES users (id)       ON DELETE CASCADE,
    role          TEXT   NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    PRIMARY KEY (collection_id, user_id)
);
CREATE INDEX IF NOT EXISTS collection_members_user_idx ON collection_members (user_id);

-- ---- Published card data (shared by every user; cached from TCGdex) --------
-- Upgrade from the first (PokeWallet-based) version: drop its catalog tables, but only
-- if no cards are owned, because card ids changed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = 'card_sets' AND column_name = 'pw_set_id') THEN
        IF to_regclass('owned_cards') IS NOT NULL AND EXISTS (SELECT 1 FROM owned_cards) THEN
            RAISE EXCEPTION 'owned_cards still has cards from the old PokeWallet catalog; remove them before upgrading';
        END IF;
        DROP TABLE IF EXISTS owned_cards, card_species, cards, card_sets CASCADE;
    END IF;
END $$;

-- The National Pokedex, loaded from app/data/species.json by scripts/init_db.py.
CREATE TABLE IF NOT EXISTS species (
    dex_number INT  PRIMARY KEY,
    name       TEXT NOT NULL,
    generation INT  NOT NULL
);

CREATE TABLE IF NOT EXISTS card_sets (
    id                  BIGSERIAL PRIMARY KEY,
    tcgdex_id           TEXT        NOT NULL UNIQUE,   -- e.g. 'swsh3'
    name                TEXT        NOT NULL,
    series              TEXT,
    set_code            TEXT,                          -- abbreviation printed on cards, e.g. 'SSP'
    release_date        DATE,
    card_count          INT,                           -- every card, including secret rares
    card_count_official INT,                           -- the number printed after the slash
    logo_url            TEXT,
    symbol_url          TEXT,
    cards_fetched_at    TIMESTAMPTZ,                   -- NULL = card list not downloaded yet
    details_synced_at   TIMESTAMPTZ,                   -- every card has its full record
    images_synced_at    TIMESTAMPTZ,                   -- every card's artwork is on disk
    sync_status         TEXT        NOT NULL DEFAULT 'idle' CHECK (sync_status IN ('idle', 'running', 'error')),
    sync_done           INT         NOT NULL DEFAULT 0,
    sync_total          INT         NOT NULL DEFAULT 0,
    sync_message        TEXT,
    sync_started_at     TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS card_sets_release_idx ON card_sets (release_date DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS cards (
    id                TEXT PRIMARY KEY,                -- TCGdex card id, e.g. 'swsh3-136'
    set_id            BIGINT REFERENCES card_sets (id) ON DELETE SET NULL,
    local_id          TEXT,                            -- number within the set
    name              TEXT NOT NULL,
    clean_name        TEXT NOT NULL,
    card_number       TEXT,                            -- as printed, e.g. '136/189'
    number_sort       INT,
    image_base        TEXT,                            -- artwork URL without quality/extension
    has_detail        BOOLEAN NOT NULL DEFAULT false,  -- full record fetched (rarity, hp, variants, dexId)
    rarity            TEXT,
    card_type         TEXT,                            -- Pokemon / Trainer / Energy
    hp                TEXT,
    stage             TEXT,
    variants          JSONB NOT NULL DEFAULT '[]',     -- finishes this card exists in
    data              JSONB NOT NULL DEFAULT '{}',
    pw_id             TEXT,                            -- matching PokeWallet card (for prices)
    prices            JSONB NOT NULL DEFAULT '{}',     -- {variant: TCGplayer market price, USD}
    prices_updated_at TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cards_set_idx  ON cards (set_id, number_sort);
CREATE INDEX IF NOT EXISTS cards_name_idx ON cards (lower(clean_name));

-- A card can feature several Pokemon (Pikachu & Zekrom-GX), so this is a join table.
CREATE TABLE IF NOT EXISTS card_species (
    card_id    TEXT NOT NULL REFERENCES cards (id)           ON DELETE CASCADE,
    dex_number INT  NOT NULL REFERENCES species (dex_number) ON DELETE CASCADE,
    PRIMARY KEY (card_id, dex_number)
);
CREATE INDEX IF NOT EXISTS card_species_dex_idx ON card_species (dex_number);

-- Cache of card-scan lookups (TCGTracking product id -> what the card is). A product never changes
-- identity, so rows are kept for good; they let a repeat scan of the same card cost one request.
CREATE TABLE IF NOT EXISTS scan_products (
    product_id BIGINT      PRIMARY KEY,
    name       TEXT        NOT NULL,
    number     TEXT,                              -- as printed, e.g. '004/191'
    rarity     TEXT,
    set_name   TEXT,                              -- e.g. 'SV08: Surging Sparks'
    set_abbr   TEXT,                              -- e.g. 'SSP'
    hint       TEXT,                              -- bracketed text from the name, e.g. 'Master Ball Pattern'
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---- What each collection owns ---------------------------------------------
-- One row per (card, variant, condition); extra copies raise `quantity`.
CREATE TABLE IF NOT EXISTS owned_cards (
    id             BIGSERIAL PRIMARY KEY,
    collection_id  BIGINT      NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
    card_id        TEXT        NOT NULL REFERENCES cards (id),
    variant        TEXT        NOT NULL,
    condition      TEXT        NOT NULL,
    quantity       INT         NOT NULL DEFAULT 1 CHECK (quantity > 0),
    acquired_on    DATE,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (collection_id, card_id, variant, condition)
);
CREATE INDEX IF NOT EXISTS owned_cards_collection_idx ON owned_cards (collection_id);
CREATE INDEX IF NOT EXISTS owned_cards_card_idx       ON owned_cards (card_id);
CREATE INDEX IF NOT EXISTS owned_cards_created_idx    ON owned_cards (collection_id, created_at DESC);

-- Master-set mode: count every finish of a card (normal, holo, reverse holo) toward completion, not just the card.
ALTER TABLE collections ADD COLUMN IF NOT EXISTS master_set BOOLEAN NOT NULL DEFAULT FALSE;

-- PriceCharting product ids (for the PriceCharting export). Loaded from a PriceCharting CSV on the Maintenance page.
CREATE TABLE IF NOT EXISTS pricecharting_products (
    id           BIGINT PRIMARY KEY,
    console_name TEXT NOT NULL,
    product_name TEXT NOT NULL
);

-- ---- Wanted list: cards a collection is still looking for ----------------------------------
-- Shown on the collection's Wanted page and, when `wanted_public` is on, on a public page that
-- needs no login. A card leaves the list automatically once a copy is added.
ALTER TABLE collections ADD COLUMN IF NOT EXISTS wanted_public BOOLEAN NOT NULL DEFAULT TRUE;
CREATE TABLE IF NOT EXISTS wanted_cards (
    collection_id BIGINT      NOT NULL REFERENCES collections (id) ON DELETE CASCADE,
    card_id       TEXT        NOT NULL REFERENCES cards (id),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, card_id)
);
