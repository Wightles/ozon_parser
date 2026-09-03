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

CREATE OR REPLACE VIEW datalens_products AS
SELECT
    sku,
    title,
    price,
    rating,
    reviews_total,
    cover_image,
    photos_seller,
    videos_seller,
    color,
    material,
    art_set,
    has_rich_content,
    parsed_at
FROM products;

CREATE OR REPLACE VIEW datalens_product_history AS
SELECT
    sku,
    price,
    rating,
    reviews_total,
    parsed_at
FROM product_history;
