-- 1. Row-level security on every table.
-- Supabase exposes the `public` schema over its REST API (PostgREST) to anyone holding
-- the anon key, which ships in the web app's bundle, and grants anon/authenticated full
-- access to new tables there by default. With RLS on and no policies, those roles see
-- nothing and can change nothing. The API connects as the tables' owner, which RLS does
-- not apply to, so it is unaffected. (Locally this is a no-op for the owner role too.)
ALTER TABLE users             ENABLE ROW LEVEL SECURITY;
ALTER TABLE posts             ENABLE ROW LEVEL SECURITY;
ALTER TABLE likes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE follows           ENABLE ROW LEVEL SECURITY;
ALTER TABLE comments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications     ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

-- 2. Indexes the queries were missing.
-- GET /posts (explore, and the signed-out home page): newest first across everyone.
CREATE INDEX IF NOT EXISTS posts_time ON posts (created_at DESC, id DESC);
-- Deleting a comment cascades to its notification; deleting a post to its notifications.
CREATE INDEX IF NOT EXISTS notifications_comment ON notifications (comment_id) WHERE comment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS notifications_post ON notifications (post_id) WHERE post_id IS NOT NULL;
