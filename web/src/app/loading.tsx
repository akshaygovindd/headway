import { Skeleton } from "@/components/ui";

export default function Loading() {
  // A page that is still streaming has no heading of its own yet; keep exactly one h1 meanwhile.
  return (
    <>
      <h1 className="sr-only">Loading</h1>
      <Skeleton />
    </>
  );
}
