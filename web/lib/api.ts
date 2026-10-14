// Thin client for the FastAPI service. Types mirror api/app/schemas.py.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Author = { id: string; username: string; display_name: string | null };

export type Post = {
  id: number;
  author: Author;
  caption: string | null;
  image_key: string;
  like_count: number;
  created_at: string;
};

export type User = {
  id: string;
  username: string;
  display_name: string | null;
  avatar_key: string | null;
  created_at: string;
  post_count: number;
  follower_count: number;
  following_count: number;
};

async function get<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export const getPosts = async () => (await get<Post[]>("/posts")) ?? [];
export const getUser = (username: string) => get<User>(`/users/${encodeURIComponent(username)}`);
