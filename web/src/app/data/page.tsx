import type { Metadata } from "next";
import { PageHeader } from "@/components/ui";
import contracts from "@/data/contracts.json";
import { REPO_URL } from "@/lib/map";

export const metadata: Metadata = {
  title: "Data contracts",
  description:
    "Schema, grain, freshness, coverage rule and quality checks for every public gold table the Headway API serves.",
  alternates: { canonical: "/data" },
};

const blobUrl = (definition: string) => `${REPO_URL}/blob/main/${definition}`;

export default function DataPage() {
  return (
    <>
      <PageHeader
        title="Data contracts"
        intro="What each public table contains, how often it refreshes, when a value is withheld and which checks guard it. Generated at build time from the repository's dbt models and API definitions."
      />
      <p className="mb-6 max-w-3xl rounded-lg border border-line bg-card p-3 text-sm" data-testid="type-note">
        Column types are <strong>derived (naming convention)</strong>, because the dbt models declare none; nullability
        is derived from <code>not_null</code> tests. Authoritative types will come from the export manifest.
      </p>
      <nav aria-label="Tables" className="mb-6">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {contracts.tables.map((t) => (
            <li key={t.name}>
              <a href={`#${t.name}`}>{t.name}</a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="grid gap-6">
        {contracts.tables.map((t) => (
          <section key={t.name} id={t.name} aria-labelledby={`h-${t.name}`} className="rounded-lg border border-line bg-card p-4">
            <h2 id={`h-${t.name}`} className="text-lg font-semibold">
              <code>{t.name}</code>
            </h2>
            <p className="mt-2 max-w-3xl">{t.description}</p>
            <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-[10rem_1fr]">
              <dt className="font-semibold">Grain</dt>
              <dd>{t.grain}</dd>
              <dt className="font-semibold">API endpoint</dt>
              <dd>
                {t.endpoints.map((e) => (
                  <code key={e} className="mr-2">
                    {e}
                  </code>
                ))}
              </dd>
              <dt className="font-semibold">Definition</dt>
              <dd>
                <a href={blobUrl(t.definition)}>{t.definition}</a>
              </dd>
              <dt className="font-semibold">Freshness</dt>
              <dd>{t.freshness}</dd>
              <dt className="font-semibold">Coverage rule</dt>
              <dd>{t.coverage}</dd>
            </dl>
            <h3 className="mb-2 mt-4 font-semibold">Columns</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Columns of {t.name}</caption>
                <thead>
                  <tr className="border-b border-line">
                    <th scope="col" className="py-2 pr-4">Column</th>
                    <th scope="col" className="py-2 pr-4">Type (derived)</th>
                    <th scope="col" className="py-2 pr-4">Nullable</th>
                    <th scope="col" className="py-2 pr-4">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {t.columns.map((c) => (
                    <tr key={c.name}>
                      <td className="border-b border-line py-2 pr-4 align-top"><code>{c.name}</code></td>
                      <td className="border-b border-line py-2 pr-4 align-top">{c.type}</td>
                      <td className="border-b border-line py-2 pr-4 align-top">{c.nullable ? "yes" : "no"}</td>
                      <td className="border-b border-line py-2 pr-4 align-top">{c.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3 className="mb-2 mt-4 font-semibold">Quality checks</h3>
            <ul className="list-disc pl-5 text-sm">
              {t.checks.map((k, i) => (
                <li key={`${k.scope}-${k.name}-${i}`}>
                  <code>{k.name}</code> on {k.scope === "table" ? "the table" : <code>{k.scope}</code>}
                  {k.detail ? ` (${k.detail})` : ""}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </>
  );
}
