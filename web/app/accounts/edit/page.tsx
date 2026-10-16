"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, type Profile, type Upload } from "@/lib/api";
import { prepareImage } from "@/lib/image";
import { useAuth } from "../../AuthProvider";
import { Avatar } from "../../Avatar";

const ACCEPTED = ["image/jpeg", "image/png", "image/webp"];
const MAX_BYTES = 25 * 1024 * 1024; // before resizing to AVATAR_SIDE
const AVATAR_SIDE = 320; // px: shown at most 84 px wide, so this is sharp on any screen
const BIO_MAX = 150;

type Fields = { username: string; display_name: string; bio: string; city: string; website: string };

const fieldsOf = (p: Profile): Fields => ({
  username: p.username,
  display_name: p.display_name ?? "",
  bio: p.bio ?? "",
  city: p.city ?? "",
  website: p.website ?? "",
});

export default function EditProfilePage() {
  const router = useRouter();
  const { ready, session, profile, refreshProfile } = useAuth();
  const [fields, setFields] = useState<Fields | null>(null);
  const [photo, setPhoto] = useState<File | null>(null); // new photo, uploaded on save
  const [removePhoto, setRemovePhoto] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else if (!profile) router.replace("/onboarding");
    else setFields((cur) => cur ?? fieldsOf(profile));
  }, [ready, session, profile, router]);

  useEffect(() => {
    if (!photo) return setPreview(null);
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  if (!ready || !session || !profile || !fields) return null;

  const set = (name: keyof Fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setFields({ ...fields, [name]: e.target.value });

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0] ?? null;
    e.target.value = ""; // picking the same file again should still fire onChange
    setError(null);
    if (!picked) return;
    if (!ACCEPTED.includes(picked.type)) return setError("Use a JPEG, PNG or WebP image.");
    if (picked.size > MAX_BYTES) return setError("That image is over 25 MB.");
    setPhoto(picked);
    setRemovePhoto(false);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const token = session!.access_token;
    setError(null);
    try {
      // Send only what changed; the API leaves everything else alone.
      const before = fieldsOf(profile!);
      const now = fields!;
      const changes: Record<string, string | null> = {};
      for (const k of Object.keys(now) as (keyof Fields)[]) {
        if (now[k].trim() !== before[k]) changes[k] = now[k];
      }
      if (photo) {
        setStatus("Uploading photo…");
        // Resized and re-encoded first, which also drops location metadata.
        const image = await prepareImage(photo, AVATAR_SIDE).catch(() => {
          throw new Error("Couldn't read that image. Try a different photo.");
        });
        const upload = await apiFetch<Upload>("/uploads", token, {
          method: "POST",
          body: JSON.stringify({ content_type: image.type, purpose: "avatar" }),
        });
        const put = await fetch(upload.upload_url, {
          method: "PUT",
          headers: { "Content-Type": image.type },
          body: image.blob,
        });
        if (!put.ok) throw new Error(`Upload failed (${put.status})`);
        changes.avatar_key = upload.key;
      } else if (removePhoto) {
        changes.avatar_key = null;
      }
      setStatus("Saving…");
      const saved = await apiFetch<Profile>("/me", token, { method: "PATCH", body: JSON.stringify(changes) });
      await refreshProfile();
      router.push(`/u/${saved.username}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setStatus(null);
    }
  }

  const shown = { username: profile.username, avatar_url: removePhoto ? null : (preview ?? profile.avatar_url) };
  const hasPhoto = Boolean(shown.avatar_url);

  return (
    <form className="form" onSubmit={onSubmit}>
      <h1>Edit profile</h1>

      <div className="avatar-edit">
        <Avatar user={shown} size={72} />
        <div>
          <label className="link avatar-pick">
            {hasPhoto ? "Change photo" : "Add a photo"}
            <input type="file" accept={ACCEPTED.join(",")} onChange={onPick} hidden />
          </label>
          {hasPhoto && (
            <button
              type="button"
              className="link danger"
              onClick={() => {
                setPhoto(null);
                setRemovePhoto(true);
              }}
            >
              Remove photo
            </button>
          )}
        </div>
      </div>

      <label>
        Name
        <input maxLength={50} value={fields.display_name} onChange={set("display_name")} />
      </label>
      <label>
        Username
        <input
          required
          pattern="[A-Za-z0-9_.]{3,30}"
          title="3-30 letters, numbers, dots or underscores"
          value={fields.username}
          onChange={set("username")}
        />
      </label>
      <label>
        Bio
        <textarea rows={3} maxLength={BIO_MAX} value={fields.bio} onChange={set("bio")} />
        <span className="hint">
          {fields.bio.length}/{BIO_MAX}
        </span>
      </label>
      <label>
        City
        <input maxLength={60} placeholder="e.g. Pune, India" value={fields.city} onChange={set("city")} />
      </label>
      <label>
        Website
        <input
          maxLength={200}
          inputMode="url"
          placeholder="example.com"
          value={fields.website}
          onChange={set("website")}
        />
      </label>

      {error && <p className="error">{error}</p>}
      <div className="form-actions">
        <button className="button" disabled={status !== null}>
          {status ?? "Save"}
        </button>
        <button type="button" className="button secondary" onClick={() => router.back()}>
          Cancel
        </button>
      </div>
    </form>
  );
}
