import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OMC Funding Tracker — Worksuite",
  description: "Omnicom Pay Run Funding — Remittance ↔ DB ↔ MoneyCorp reconciliation",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Terrane+Sans:wght@300;400;600;700;900&family=Trust+Serif:ital@0;1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
