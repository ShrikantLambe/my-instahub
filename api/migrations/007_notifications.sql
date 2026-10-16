-- Activity notifications: someone liked or commented on your post, or followed you.
-- Rows are written after the action commits (a side effect, not part of the action).
-- Undoing a like or follow deletes its notification; deleting a comment cascades.
CREATE TABLE notifications (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  recipient_id uuid   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  actor_id     uuid   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         text   NOT NULL CHECK (kind IN ('like', 'comment', 'follow')),
  post_id      bigint REFERENCES posts(id) ON DELETE CASCADE,
  comment_id   bigint REFERENCES comments(id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  read_at      timestamptz,
  CHECK (recipient_id <> actor_id),
  CHECK ((kind = 'follow') = (post_id IS NULL)),
  CHECK ((kind = 'comment') = (comment_id IS NOT NULL))
);
-- The activity page: newest first, paged by cursor.
CREATE INDEX notifications_inbox ON notifications (recipient_id, created_at DESC, id DESC);
-- The unread badge counts only these.
CREATE INDEX notifications_unread ON notifications (recipient_id) WHERE read_at IS NULL;
-- One like / one follow notification per actor, so like-unlike-like can't spam.
CREATE UNIQUE INDEX notifications_one_like ON notifications (recipient_id, actor_id, post_id) WHERE kind = 'like';
CREATE UNIQUE INDEX notifications_one_follow ON notifications (recipient_id, actor_id) WHERE kind = 'follow';
