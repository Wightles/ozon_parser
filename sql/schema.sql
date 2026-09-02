CREATE TABLE IF NOT EXISTS products (
    sku VARCHAR PRIMARY KEY,
    title TEXT,
    price NUMERIC,
    rating DOUBLE PRECISION,
    reviews_total INTEGER,
    cover_image TEXT,
    photos_seller INTEGER NOT NULL DEFAULT 0,
    videos_seller INTEGER NOT NULL DEFAULT 0,
    color TEXT,
    material TEXT,
    art_set TEXT,
    has_rich_content BOOLEAN NOT NULL DEFAULT FALSE,
    parsed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS product_history (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR NOT NULL REFERENCES products (sku) ON DELETE CASCADE,
    price NUMERIC,
    rating DOUBLE PRECISION,
    reviews_total INTEGER,
    parsed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_product_history_sku_parsed_at
    ON product_history (sku, parsed_at);
