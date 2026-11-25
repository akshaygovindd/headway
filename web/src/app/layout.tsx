import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Headway: MBTA subway reliability", template: "%s | Headway" },
  description:
    "How reliable is the Boston subway right now? Open measurements with their coverage and freshness shown beside every number.",
};

const NAV = [
  ["/", "Lines"],
  ["/incidents", "Incidents"],
  ["/forecast", "Forecast"],
  ["/pipeline", "Pipeline health"],
  ["/how-we-measure", "How we measure"],
  ["/about", "About"],
] as const;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{var t=localStorage.getItem("theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}',
          }}
        />
      </head>
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-10 focus:rounded focus:bg-bg focus:p-2"
        >
          Skip to content
        </a>
        <header className="border-b border-line">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <Link href="/" className="text-lg font-bold text-fg no-underline">
              Headway
            </Link>
            <nav aria-label="Main">
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                {NAV.map(([href, label]) => (
                  <li key={href}>
                    <Link href={href}>{label}</Link>
                  </li>
                ))}
              </ul>
            </nav>
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className="mx-auto max-w-6xl px-4 py-6">
          {children}
        </main>
        <footer className="mx-auto max-w-6xl border-t border-line px-4 py-6 text-sm text-muted">
          Headway approximates MBTA service from public feeds. It is not an official MBTA measure.
        </footer>
      </body>
    </html>
  );
}
