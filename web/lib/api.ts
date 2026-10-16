// Thin client for the FastAPI service. Types mirror api/app/schemas.py.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Author = {
  id: string;
  username: string;
  display_name: string | null;
  avatar_url?: string | null; // optional: older API deployments don't send it
};

export type Post = {
  id: number;
  author: Author;
  caption: string | null;
  image_key: string;
  image_url: string;
  image_width: number | null;
  image_height: number | null;
  like_count: number;
  like_version: number;
  viewer_has_liked: boolean;
  comment_count: number;
  comment_version: number;
  created_at: string;
};

export type User = {
  id: string;
  username: string;
  display_name: string | null;
  avatar_key: string | null;
  avatar_url?: string | null;
  bio?: string | null;
  city?: string | null;
  website?: string | null;
  created_at: string;
  post_count: number;
  follower_count: number;
  following_count: number;
  viewer_follows: boolean;
};

export type Profile = {
  id: string;
  username: string;
  display_name: string | null;
  avatar_key?: string | null;
  avatar_url?: string | null;
  bio?: string | null;
  city?: string | null;
  website?: string | null;
};

export type Upload = { upload_url: string; key: string; expires_in: number };

export type LikeResult = { post_id: number; liked: boolean; like_count: number; like_version: number };

export type FollowResult = { username: string; following: boolean; follower_count: number };

export type FeedPage = { posts: Post[]; next_cursor: string | null };

export type Comment = { id: number; post_id: number; author: Author; body: string; created_at: string };

export type CommentPage = {
  comments: Comment[]; // newest first
  next_cursor: string | null;
  comment_count: number;
  comment_version: number;
};

// What POST /posts/{id}/comments, DELETE /comments/{id} and the live stream send.
export type CommentChange = {
  post_id: number;
  comment?: Comment | null;
  deleted_id?: number | null;
  comment_count: number;
  comment_version: number;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json() as Promise<T>;
}

export const getPosts = async () => (await get<Post[]>("/posts")) ?? [];
export const getPost = (id: string) => get<Post>(`/posts/${encodeURIComponent(id)}`);
export const getUser = (username: string) => get<User>(`/users/${encodeURIComponent(username)}`);
export const getComments = (postId: number, before?: string) =>
  get<CommentPage>(
    `/posts/${postId}/comments` + (before ? `?before=${encodeURIComponent(before)}` : ""),
  );
export const getUserPosts = async (username: string) =>
  (await get<Post[]>(`/users/${encodeURIComponent(username)}/posts`)) ?? [];

// FastAPI errors are {detail: string} or, for validation, {detail: [{msg}, ...]}.
// Pydantic prefixes validator messages with "Value error, ", which users needn't see.
function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => String(d?.msg ?? d).replace(/^Value error, /, "")).join("; ");
  }
  return `Request failed (${status})`;
}

// Signed-in calls: the Supabase access token is the only proof of who the user is.
export async function apiFetch<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, errorMessage(body, res.status));
  return body as T;
}

export type Notification = {
  id: number;
  kind: "like" | "comment" | "follow";
  actor: Author;
  post: { id: number; image_url: string } | null;
  comment: { id: number; body: string } | null;
  created_at: string;
  read: boolean;
};

export type NotificationPage = {
  notifications: Notification[]; // newest first
  next_cursor: string | null;
  unread_count: number;
};
