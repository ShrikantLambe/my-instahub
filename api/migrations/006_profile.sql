-- Profile details shown on the profile page. users.avatar_key already exists (001).
-- Limits match Instagram's: a 150-character bio.
ALTER TABLE users
  ADD COLUMN bio     text CHECK (char_length(bio) <= 150),
  ADD COLUMN city    text CHECK (char_length(city) <= 60),
  ADD COLUMN website text CHECK (char_length(website) <= 200);
