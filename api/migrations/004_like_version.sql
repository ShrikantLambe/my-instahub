-- Bumped in the same statement as like_count. Copies of the count (the Redis cache,
-- live updates in browsers) carry this version and refuse to go backwards, so a
-- slow, out-of-order write can never overwrite a newer count with an older one.
ALTER TABLE posts ADD COLUMN like_version bigint NOT NULL DEFAULT 0;
