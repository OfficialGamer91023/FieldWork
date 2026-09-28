import type { Metadata } from "next";
import SiteHeader from "./SiteHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fieldwork: voice user interviews that synthesize themselves",
  description:
    "An AI interviewer holds adaptive voice conversations with your users, then ranks the themes across every interview, each backed by the exact quotes.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <SiteHeader />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
