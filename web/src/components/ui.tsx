import Link from "next/link";
import type { ReactNode } from "react";
import type { ApiFailure } from "@/lib/api";
import { methodologyUrl } from "@/lib/map";

export function PageHeader({
  title,
  intro,
}: {
  title: string;
  intro?: ReactNode;
}) {
  return (
    <header className="mb-6">
      <h1 className="text-2xl font-bold sm:text-3xl">{title}</h1>
      {intro ? <p className="mt-2 max-w-3xl text-muted">{intro}</p> : null}
    </header>
  );
}

export function Card({ title, definition, children }: { title: string; definition?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-card p-4" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {definition ? (
          <a href={methodologyUrl(definition)} className="text-sm">
            How this is measured
          </a>
        ) : null}
      </div>
      {children}
    </section>
  );
}

/** A friendly message instead of a crash when the API is down or a table is not published. */
export function ErrorState({ failure, what }: { failure: ApiFailure; what?: string }) {
  const notYet = failure.kind === "not_available";
  return (
    <div role={notYet ? "status" : "alert"} className="rounded-lg border border-line bg-card p-4" data-testid="error-state">
      <p className="font-semibold">
        Data unavailable right now{what ? `: ${what}` : ""}
      </p>
      <p className="mt-1 text-sm text-muted">
        {notYet
          ? "It has not been published yet and will appear here once the pipeline has produced it."
          : "We can't reach the data service. Nothing is wrong with your connection. Try again in a few minutes; this page refreshes itself."}
      </p>
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading</span>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: rows * 2 }, (_, i) => (
          <div key={i} className="h-28 animate-pulse rounded-lg border border-line bg-card" />
        ))}
      </div>
    </div>
  );
}

export function Table({
  caption,
  head,
  children,
}: {
  caption: string;
  head: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line">
            {head.map((h) => (
              <th key={h} scope="col" className="py-2 pr-4 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, colSpan }: { children: ReactNode; colSpan?: number }) {
  return <td colSpan={colSpan} className="border-b border-line py-2 pr-4 align-top">{children}</td>;
}

export function LineLink({ line }: { line: string }) {
  return (
    <Link href={`/lines/${line}`} className="font-medium">
      {line}
    </Link>
  );
}
