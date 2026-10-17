import type { ComponentProps } from "react";

import { cn } from "cn";

export function Badge({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-md border px-2 py-0.5 text-[13px] font-medium whitespace-nowrap",
        className,
      )}
      {...props}
    />
  );
}
