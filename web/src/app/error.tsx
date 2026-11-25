"use client";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-line bg-card p-4">
      <p className="font-semibold">Something went wrong showing this page.</p>
      <p className="mt-1 text-sm text-muted">The data may be briefly unavailable. Try again in a moment.</p>
      <button type="button" onClick={reset} className="mt-3 rounded border border-line px-3 py-1">
        Try again
      </button>
    </div>
  );
}
