-- Core schema from design.md. likes PK (post_id, user_id) is the idempotency guarantee.

CREATE TABLE users (
  id           uuid PRIMARY KEY,          -- same id as Supabase Auth
  username     text UNIQUE NOT NULL,
  display_name text,
  avatar_key   text,                      -- R2 object key, not the image
  created_at   timestamptz DEFAULT now()
);

CREATE TABLE posts (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  author_id   uuid NOT NULL REFERENCES users(id),
  caption     text,
  image_key   text NOT NULL,              -- e.g. posts/2026/10/abc.jpg in R2
  like_count  integer NOT NULL DEFAULT 0, -- denormalized counter
  created_at  timestamptz DEFAULT now()
);
CREATE INDEX posts_author_time ON posts (author_id, created_at DESC);

CREATE TABLE likes (
  post_id    bigint REFERENCES posts(id) ON DELETE CASCADE,
  user_id    uuid   REFERENCES users(id),
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (post_id, user_id)          -- one like per user per post
);
CREATE INDEX likes_user ON likes (user_id, created_at DESC);

CREATE TABLE follows (
  follower_id uuid REFERENCES users(id),
  followee_id uuid REFERENCES users(id),
  created_at  timestamptz DEFAULT now(),
  PRIMARY KEY (follower_id, followee_id)
);
CREATE INDEX follows_followee ON follows (followee_id);
