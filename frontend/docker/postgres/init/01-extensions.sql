-- Runs once, on first container start with an empty data volume.
-- Alembic migrations handle everything else; this is only for extensions,
-- which need to exist before any migration references their types.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email column
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector, entity embeddings
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- trigram index for insight text search
