import type { Metadata } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import type { ReactNode } from "react";
import { AsOf } from "@/components/AsOf";
import { NavLinks } from "@/components/NavLinks";
import { MetricPopover } from "@/components/MetricPopover";
import { docUrl } from "@/lib/definitions";
import { REPO_URL } from "@/lib/map";
import { siteUrl } from "@/lib/site";
import "./globals.css";

const overpass = localFont({ src: "../fonts/Overpass.woff2", variable: "--font-overpass", weight: "700 800", display: "swap" });
const inter = localFont({ src: "../fonts/Inter.woff2", variable: "--font-inter", weight: "100 900", display: "swap" });
const mono = localFont({ src: "../fonts/JetBrainsMono.woff2", variable: "--font-jbmono", weight: "100 800", display: "swap" });

const DESCRIPTION =
  "How reliable is the Boston subway right now? Open measurements with their coverage and freshness shown beside every number.";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { default: "Headway: MBTA subway reliability", template: "%s | Headway" },
  description: DESCRIPTION,
  openGraph: { siteName: "Headway", type: "website", description: DESCRIPTION },
  twitter: { card: "summary_large_image" },
};

const NAV = [
  ["/", "Lines"],
  ["/#accuracy", "Accuracy"],
  ["/incidents", "Incidents"],
  ["/slow-zones", "Slow zones"],
  ["/accessibility", "Elevators"],
  ["/forecast", "Forecast"],
  ["/pipeline", "Pipeline"],
  ["/how-we-measure", "Methodology"],
] as const;

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${overpass.variable} ${inter.variable} ${mono.variable}`}>
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-10 focus:rounded focus:bg-card focus:p-2"
        >
          Skip to content
        </a>
        <header className="border-b border-line bg-card">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-4 pt-3">
            <div>
              <Link href="/" className="font-display text-xl font-extrabold text-fg no-underline">
                Headway
              </Link>
              <p className="text-xs text-muted">Independent project · not affiliated with the MBTA</p>
            </div>
            <div className="sm:ml-auto sm:text-right">
              <AsOf />
            </div>
            <nav aria-label="Main" className="w-full min-w-0">
              <NavLinks items={NAV} repoUrl={REPO_URL} />
            </nav>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-6xl px-4 py-6">
          {children}
        </main>
        <footer className="mx-auto max-w-6xl border-t border-line px-4 py-6 text-sm text-muted">
          <p>Headway approximates MBTA service from public feeds. It is not an official MBTA measure. Times in ET.</p>
          <ul className="mt-2 flex flex-wrap gap-x-5">
            <li><a href={REPO_URL} className="tap">GitHub</a></li>
            <li><Link href="/how-we-measure" className="tap">Methodology</Link></li>
            <li><a href={docUrl("docs/API.md")} className="tap">API</a></li>
            <li><Link href="/data" className="tap">Data contracts</Link></li>
            <li><Link href="/about" className="tap">About</Link></li>
          </ul>
        </footer>
        <MetricPopover />
      </body>
    </html>
  );
}
