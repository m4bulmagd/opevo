"use client";

import { type ReactNode, useRef, useState } from "react";

import Link from "next/link";

import { Menu, PhoneCall, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { WorkspaceNavigation } from "@/components/workspace/workspace-navigation";

type MobileWorkspaceNavigationProps = {
  accountControl: ReactNode;
};

export function MobileWorkspaceNavigation({ accountControl }: MobileWorkspaceNavigationProps) {
  const firstDestinationRef = useRef<HTMLAnchorElement>(null);
  const [open, setOpen] = useState(false);

  return (
    <Sheet onOpenChange={setOpen} open={open}>
      <SheetTrigger asChild>
        <Button aria-label="Open navigation" className="min-h-11 min-w-11 xl:hidden" size="icon" variant="outline">
          <Menu aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent
        className="w-72 max-w-[calc(100vw-1rem)] border-sidebar-border bg-sidebar p-0 shadow-raised"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          firstDestinationRef.current?.focus();
        }}
        showCloseButton={false}
        side="left"
      >
        <SheetTitle className="sr-only">Workspace navigation</SheetTitle>
        <SheetDescription className="sr-only">Navigate between Opevo workspace destinations.</SheetDescription>
        <div className="flex h-full flex-col gap-6 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] text-sidebar-foreground">
          <div className="flex items-center justify-between gap-3">
            <Link
              aria-label="Opevo overview"
              className="flex min-h-11 min-w-0 items-center gap-3 rounded-lg px-2 py-1.5 outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50"
              href="/dashboard"
              onClick={() => setOpen(false)}
              prefetch={false}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
                <PhoneCall aria-hidden="true" className="size-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate font-semibold text-sm tracking-tight">Opevo</span>
                <span className="block truncate text-muted-foreground text-xs">AI Call Assistant</span>
              </span>
            </Link>
            <SheetClose asChild>
              <Button aria-label="Close navigation" className="min-h-11 min-w-11" size="icon" variant="ghost">
                <X aria-hidden="true" />
              </Button>
            </SheetClose>
          </div>
          <WorkspaceNavigation
            ariaLabel="Mobile workspace destinations"
            firstDestinationRef={firstDestinationRef}
            onNavigate={() => setOpen(false)}
          />
          <div className="mt-auto flex flex-col gap-4">
            <Separator />
            <div>{accountControl}</div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
