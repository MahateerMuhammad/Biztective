"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postJson, resetCsrfToken } from "@/lib/api/client";

/** Ends the session. Returns false when the server did not confirm it, so callers never pretend it worked. */
export async function signOut(): Promise<boolean> {
  try {
    await postJson("/api/v1/auth/logout");
  } catch {
    return false;
  }
  resetCsrfToken();
  return true;
}

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);

  async function logout() {
    setPending(true);
    setFailed(false);
    if (!(await signOut())) {
      // The session may still be valid, so do not pretend the sign-out succeeded.
      setFailed(true);
      setPending(false);
      return;
    }
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-2">
      {failed ? (
        <span role="alert" className="text-sm text-destructive">
          Sign-out failed. Try again.
        </span>
      ) : null}
      <Button variant="outline" size="sm" onClick={logout} disabled={pending}>
        Sign out
      </Button>
    </div>
  );
}
