import { getPosts } from "@/lib/api";
import { HomeFeed } from "./HomeFeed";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  // Rendered for everyone; signed-in viewers switch to their following feed on the client.
  const explore = await getPosts();
  return <HomeFeed explore={explore} />;
}
