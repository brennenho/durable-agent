"use client";

import { useRef, useState, type ReactNode } from "react";
import { Info } from "lucide-react";
import { Tooltip } from "radix-ui";

export function InfoHint({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root open={open} onOpenChange={setOpen}>
        <Tooltip.Trigger
          ref={trigger}
          type="button"
          aria-label={label}
          className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          // Keep pointer-down from closing the hint before a tap can toggle it.
          onPointerDown={(event) => event.preventDefault()}
          onClick={(event) => {
            event.preventDefault();
            setOpen((value) => !value);
          }}
        >
          <Info aria-hidden="true" className="size-3.5" />
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            sideOffset={6}
            collisionPadding={12}
            onPointerDownOutside={(event) => {
              if (trigger.current?.contains(event.target as Node)) event.preventDefault();
            }}
            className="z-50 max-w-[min(18rem,calc(100vw-2rem))] rounded-md border bg-background px-3 py-2 text-xs leading-relaxed font-normal text-foreground shadow-md"
          >
            {children}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
