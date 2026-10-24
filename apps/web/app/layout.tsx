import "./globals.css";
import { Banner } from "../components/banner";

export const metadata = {
  title: "Acme procurement",
  description: "Operations console for the Acme Computing procurement agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Banner />
        <div className="shell">{children}</div>
      </body>
    </html>
  );
}
