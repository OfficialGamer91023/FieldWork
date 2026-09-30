import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans, Instrument_Serif } from "next/font/google";
import SiteHeader from "./SiteHeader";
import "./globals.css";

const serif = Instrument_Serif({ weight: "400", style: ["normal", "italic"], subsets: ["latin"], variable: "--font-serif" });
const sans = IBM_Plex_Sans({ weight: ["400", "500", "600"], subsets: ["latin"], variable: "--font-sans" });
const mono = IBM_Plex_Mono({ weight: "500", subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Fieldwork: voice user interviews that synthesize themselves",
  description:
    "An AI interviewer holds adaptive voice conversations with your users, then ranks the themes across every interview, each backed by the exact quotes.",
};

// Runs before first paint so a theme picked with the header toggle doesn't flash.
const themeScript = `try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${serif.variable} ${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <SiteHeader />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
