import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RedovisningAI",
  description: "Granskning och rådgivning för redovisningsbyråer",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="sv">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
