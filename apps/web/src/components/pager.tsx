import Link from "next/link";

/** Keyset pagination: lists load one page at a time from the server, never whole tables. */
export function Pager({ path, cursor, next }: { path: string; cursor?: string; next: string | null }) {
  if (!cursor && !next) return null;
  return (
    <nav aria-label="Pagination" className="flex items-center gap-3 pt-3 text-sm">
      {cursor ? (
        <Link href={path} className="font-medium underline-offset-4 hover:underline">
          ← First page
        </Link>
      ) : null}
      {next ? (
        <Link href={`${path}?cursor=${encodeURIComponent(next)}`} className="font-medium underline-offset-4 hover:underline">
          Next page →
        </Link>
      ) : null}
    </nav>
  );
}

/** The cursor query parameter, accepted only in the opaque form the API produces. */
export function cursorParam(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,200}$/.test(value) ? value : undefined;
}
