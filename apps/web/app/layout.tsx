import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "WorldState",
  description: "A time machine for finance agents. Trace every decision to what was knowable at the time.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="top">
            <div>
              <div className="eyebrow">WorldState</div>
              <h1>Historical evidence, not hindsight</h1>
            </div>
            <nav className="row">
              <a href="/">Cases</a>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
