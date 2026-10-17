import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/** False in the server render and until React has hydrated, then true. Submit buttons stay
 * disabled until then, so an early click can never fall back to a plain form submission that
 * reloads the page and silently drops what was typed. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
