-- Original pixel size of each photo, so clients can reserve the right space (no
-- layout jump) and show the whole image at its own shape. Nullable: older posts
-- don't have it, and clients fall back to measuring the image once it loads.
ALTER TABLE posts
  ADD COLUMN image_width  integer CHECK (image_width  > 0),
  ADD COLUMN image_height integer CHECK (image_height > 0);
