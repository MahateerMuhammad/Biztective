import { LoadingSkeleton } from "@/components/page";

/** Shown while a business screen is rendered on the server. Same page margins and a card-shaped block, so little shifts when it arrives. */
export default function Loading() {
  return (
    <div className="grid gap-6" aria-busy="true">
      <div className="grid gap-2">
        <div className="h-9 w-64 animate-pulse rounded-md bg-skeleton motion-reduce:animate-none" />
        <div className="h-5 w-48 animate-pulse rounded-md bg-skeleton motion-reduce:animate-none" />
      </div>
      <div className="rounded-xl bg-card p-6 ring-1 ring-foreground/10">
        <LoadingSkeleton lines={5} />
      </div>
    </div>
  );
}
