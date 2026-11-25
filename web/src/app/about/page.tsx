import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { PageHeader } from "@/components/ui";
import { REPO_URL } from "@/lib/map";

export const metadata: Metadata = pageMeta("About", "What Headway is, who it is not affiliated with, and where the source and data live.", "/about");

export default function About() {
  return (
    <>
      <PageHeader title="About Headway" />
      <div className="grid max-w-3xl gap-4">
        <p>
          Headway measures how reliable the MBTA subway is, from the public real-time feeds, and
          publishes the results as open data. It shows what riders feel: trains arriving at the
          spacing they promise, predictions that come true, and incidents that were more than a blip.
        </p>
        <p>
          Every number is shown with its coverage, the share of scheduled trips Headway could observe.
          Under 50% the number is withheld. Pages say how old the data is. Hours when Headway&apos;s own
          collection had a gap are labelled as such and never counted as a service failure.
        </p>
        <p>
          Headway is an independent project and is not affiliated with the MBTA. Its figures are
          approximations, not official measures.
        </p>
        <p>
          Source code and definitions: <a href={REPO_URL}>the Headway repository</a>. The same data is
          available from the public API; see the API documentation in the repository.
        </p>
      </div>
    </>
  );
}
