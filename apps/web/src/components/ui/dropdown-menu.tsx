"use client";

import { Menu } from "@base-ui/react/menu";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "cn";

/** shadcn-style dropdown menu on Base UI's Menu: focus management, arrow keys, Escape and type-ahead come with it. */
export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;
export const DropdownMenuGroup = Menu.Group;

export function DropdownMenuContent({
  className,
  align = "end",
  sideOffset = 8,
  children,
}: {
  className?: string;
  align?: "start" | "center" | "end";
  sideOffset?: number;
  children: ReactNode;
}) {
  return (
    <Menu.Portal>
      <Menu.Positioner align={align} sideOffset={sideOffset} className="z-50">
        <Menu.Popup
          className={cn(
            "min-w-60 origin-(--transform-origin) rounded-xl border border-border bg-popover p-1.5 text-popover-foreground shadow-lg outline-none transition-[opacity,scale] data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0 motion-reduce:transition-none",
            className,
          )}
        >
          {children}
        </Menu.Popup>
      </Menu.Positioner>
    </Menu.Portal>
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof Menu.GroupLabel>) {
  return <Menu.GroupLabel className={cn("px-2.5 py-2 text-sm text-muted-foreground", className)} {...props} />;
}

export function DropdownMenuItem({ className, ...props }: ComponentProps<typeof Menu.Item>) {
  return (
    <Menu.Item
      className={cn(
        "flex min-h-11 cursor-default items-center gap-2 rounded-lg px-2.5 text-[15px] font-medium outline-none select-none data-[highlighted]:bg-muted data-[highlighted]:text-foreground [&_svg]:size-4 [&_svg]:shrink-0",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof Menu.Separator>) {
  return <Menu.Separator className={cn("-mx-1.5 my-1.5 h-px bg-border", className)} {...props} />;
}
