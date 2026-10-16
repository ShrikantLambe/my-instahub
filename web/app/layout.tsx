import type { Metadata } from "next";
import Link from "next/link";
import { AuthProvider } from "./AuthProvider";
import { NavAuth } from "./NavAuth";
import { NotificationsProvider } from "./NotificationsProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "instahub",
  description: "A small Instagram-style app with a real architecture",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <NotificationsProvider>
            <nav className="nav">
              <Link href="/" className="brand">instahub</Link>
              <NavAuth />
            </nav>
            <main className="main">{children}</main>
          </NotificationsProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
