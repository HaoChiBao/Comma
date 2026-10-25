import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Comma — What was knowable at the time",
  description: "A time machine for AI agents. Trace every decision to what was knowable at the time.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
