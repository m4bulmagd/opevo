"use client";

import { useRef, useState } from "react";

import Link from "next/link";

import { Ellipsis, X } from "lucide-react";

import { BottomSheet } from "@/components/motion/bottom-sheet";
import { Button } from "@/components/ui/button";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { isDashboardItemActive, type NavItem } from "@/navigation/dashboard-items";

type MobileMoreSheetProps = {
  items: NavItem[];
  pathname: string;
};

export function MobileMoreSheet({ items, pathname }: MobileMoreSheetProps) {
  const firstDestinationRef = useRef<HTMLAnchorElement>(null);
  const [open, setOpen] = useState(false);

  return (
    <Drawer onOpenChange={setOpen} open={open}>
      <DrawerTrigger asChild>
        <Button
          aria-label="More"
          className="min-h-11 min-w-11 flex-col gap-1 rounded-md px-1 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-foreground"
          variant="ghost"
        >
          <Ellipsis aria-hidden="true" />
          <span className="text-xs">More</span>
        </Button>
      </DrawerTrigger>
      {open ? (
        <DrawerContent
          className="border-border bg-surface-elevated p-0 shadow-overlay"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            firstDestinationRef.current?.focus();
          }}
        >
          <BottomSheet>
            <DrawerHeader className="items-start gap-1 px-5 pt-5 pb-3 text-left">
              <DrawerTitle>More workspace destinations</DrawerTitle>
              <DrawerDescription>Open billing or manage your account.</DrawerDescription>
            </DrawerHeader>
            <nav aria-label="More workspace navigation" className="flex flex-col gap-1 px-3">
              {items.map((item, index) => {
                const active = isDashboardItemActive(pathname, item.href);

                return (
                  <Link
                    aria-current={active ? "page" : undefined}
                    aria-label={item.title}
                    className="flex min-h-11 items-center gap-3 rounded-md px-3 font-medium text-sm outline-none transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                    href={item.href}
                    key={item.href}
                    prefetch={false}
                    ref={index === 0 ? firstDestinationRef : undefined}
                  >
                    <item.icon aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
                    <span className="truncate">{item.title}</span>
                  </Link>
                );
              })}
            </nav>
            <DrawerFooter className="px-3 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
              <DrawerClose asChild>
                <Button className="min-h-11" variant="outline">
                  <X data-icon="inline-start" />
                  Close
                </Button>
              </DrawerClose>
            </DrawerFooter>
          </BottomSheet>
        </DrawerContent>
      ) : null}
    </Drawer>
  );
}
