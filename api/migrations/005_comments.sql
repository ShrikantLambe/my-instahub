-- Comments: flat (no reply threads), newest first. posts.comment_count is a denormalized
-- copy kept in the same statement as the insert/delete, like like_count, and
-- comment_version lets live copies of the count refuse to go backwards.
CREATE TABLE comments (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  post_id     bigint NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  author_id   uuid   NOT NULL REFERENCES users(id),
  body        text   NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2200),
  created_at  timestamptz NOT NULL DEFAULT now()
);
-- Serves "comments on this post, newest first" with cursor paging.
CREATE INDEX comments_post_time ON comments (post_id, created_at DESC, id DESC);

ALTER TABLE posts
  ADD COLUMN comment_count   integer NOT NULL DEFAULT 0,
  ADD COLUMN comment_version bigint  NOT NULL DEFAULT 0;
