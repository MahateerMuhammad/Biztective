/** Shown while a business screen is rendered on the server; no layout shift when it arrives. */
export default function Loading() {
  return (
    <div role="status" aria-live="polite" className="grid gap-4">
      <span className="sr-only">Loading…</span>
      <div className="h-7 w-64 animate-pulse rounded-md bg-muted motion-reduce:animate-none" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
    </div>
  );
}
