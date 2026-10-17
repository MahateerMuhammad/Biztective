"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** A failed server render: say so plainly and offer a retry. Nothing is shown as succeeded. */
export default function BusinessError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <Alert variant="destructive" role="alert">
      <AlertTitle>This page could not be loaded</AlertTitle>
      <AlertDescription className="grid gap-3">
        <p>The server did not answer as expected. Nothing was changed. Try again in a moment.</p>
        <div>
          <Button type="button" variant="outline" onClick={reset}>
            Try again
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
