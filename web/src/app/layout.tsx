import type { Metadata } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import type { ReactNode } from "react";
import { AsOf } from "@/components/AsOf";
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
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <div>
              <Link href="/" className="font-display text-xl font-extrabold text-fg no-underline">
                Headway
              </Link>
              <p className="text-xs text-muted">Independent project · not affiliated with the MBTA</p>
            </div>
            <div className="sm:ml-auto sm:text-right">
              <AsOf />
            </div>
            <nav aria-label="Main" className="w-full">
              <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm font-medium">
                {NAV.map(([href, label]) => (
                  <li key={href}>
                    <Link href={href} className="inline-block py-2">
                      {label}
                    </Link>
                  </li>
                ))}
                <li>
                  <a href={REPO_URL} className="inline-block py-2">
                    GitHub
                  </a>
                </li>
              </ul>
            </nav>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-6xl px-4 py-6">
          {children}
        </main>
        <footer className="mx-auto max-w-6xl border-t border-line px-4 py-6 text-sm text-muted">
          <p>Headway approximates MBTA service from public feeds. It is not an official MBTA measure. Times in ET.</p>
          <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
            <li><a href={REPO_URL}>GitHub</a></li>
            <li><Link href="/how-we-measure">Methodology</Link></li>
            <li><a href={docUrl("docs/API.md")}>API</a></li>
            <li><Link href="/data">Data contracts</Link></li>
            <li><Link href="/about">About</Link></li>
          </ul>
        </footer>
        <MetricPopover />
      </body>
    </html>
  );
}
