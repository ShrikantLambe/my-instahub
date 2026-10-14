import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "instahub",
  description: "A small Instagram-style app with a real architecture",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="nav">
          <Link href="/" className="brand">instahub</Link>
        </nav>
        <main className="main">{children}</main>
      </body>
    </html>
  );
}
