"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** A failed server render: say so plainly and offer a retry. Nothing is shown as succeeded. */
export default function BusinessError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <Alert variant="destructive" role="alert" className="p-6">
      <AlertTitle className="text-base">We couldn’t load this page</AlertTitle>
      <AlertDescription className="grid gap-3 text-[15px]">
        <p>Nothing was changed. Try again in a moment.</p>
        <div>
          <Button type="button" variant="outline" onClick={reset}>
            Try again
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
