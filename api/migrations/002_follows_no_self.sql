-- Following yourself is meaningless; let the database refuse it, not just the API.
ALTER TABLE follows ADD CONSTRAINT follows_no_self CHECK (follower_id <> followee_id);
