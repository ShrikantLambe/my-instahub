"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { apiFetch, type Post, type Upload } from "@/lib/api";
import { useAuth } from "../AuthProvider";

const ACCEPTED = ["image/jpeg", "image/png", "image/webp"];
const MAX_BYTES = 10 * 1024 * 1024;

export default function NewPostPage() {
  const router = useRouter();
  const { ready, session, profile } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [caption, setCaption] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else if (!profile) router.replace("/onboarding");
  }, [ready, session, profile, router]);

  useEffect(() => {
    if (!file) return setPreview(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  if (!ready || !session || !profile) return null;

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0] ?? null;
    setError(null);
    if (picked && !ACCEPTED.includes(picked.type)) return setError("Use a JPEG, PNG or WebP image.");
    if (picked && picked.size > MAX_BYTES) return setError("That image is over 10 MB.");
    setFile(picked);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    const token = session!.access_token;
    setError(null);
    try {
      // 1. Ask the API for a short-lived upload URL.
      setStatus("Preparing upload…");
      const upload = await apiFetch<Upload>("/uploads", token, {
        method: "POST",
        body: JSON.stringify({ content_type: file.type }),
      });
      // 2. Send the bytes straight to storage; they never pass through the API.
      setStatus("Uploading photo…");
      const put = await fetch(upload.upload_url, {
        method: "PUT",
        headers: { "Content-Type": file.type },
        body: file,
      });
      if (!put.ok) throw new Error(`Upload failed (${put.status})`);
      // 3. Create the post pointing at the uploaded object.
      setStatus("Posting…");
      await apiFetch<Post>("/posts", token, {
        method: "POST",
        body: JSON.stringify({ image_key: upload.key, caption: caption || null }),
      });
      router.push("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setStatus(null);
    }
  }

  return (
    <form className="form" onSubmit={onSubmit}>
      <h1>New post</h1>
      <label>
        Photo
        <input type="file" accept={ACCEPTED.join(",")} required onChange={onPick} />
      </label>
      {preview && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="photo" src={preview} alt="Preview of the selected photo" />
      )}
      <label>
        Caption
        <textarea maxLength={2200} rows={3} value={caption} onChange={(e) => setCaption(e.target.value)} />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="button" disabled={!file || status !== null}>
        {status ?? "Share"}
      </button>
    </form>
  );
}
