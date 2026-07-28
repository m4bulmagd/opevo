"use client";

import { useState } from "react";

import { Bell } from "lucide-react";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const PREVIEW_NOTIFICATIONS = [
  {
    id: "call-summary",
    title: "Call summary ready",
    body: "Aperçu local pour l’appel de Claire Martin.",
    time: "Il y a 2 min",
  },
  {
    id: "follow-up",
    title: "Suivi à vérifier",
    body: "Une demande de rappel attend votre validation.",
    time: "Il y a 18 min",
  },
  {
    id: "weekly-usage",
    title: "Point d’usage hebdomadaire",
    body: "Consultez l’aperçu de l’activité de votre accueil.",
    time: "Hier",
  },
] as const;

export function WorkspaceNotificationsPreview() {
  const [unreadIds, setUnreadIds] = useState<ReadonlySet<string>>(
    () => new Set(PREVIEW_NOTIFICATIONS.map((notification) => notification.id)),
  );
  const unreadCount = unreadIds.size;

  function markRead(id: string) {
    setUnreadIds((currentIds) => {
      const nextIds = new Set(currentIds);
      nextIds.delete(id);
      return nextIds;
    });
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          aria-label={`Notifications (${unreadCount} unread)`}
          className="relative min-h-11 min-w-11"
          size="icon"
          variant="outline"
        >
          <Bell aria-hidden="true" />
          {unreadCount > 0 ? (
            <span
              aria-hidden="true"
              className="absolute -top-1 -right-1 inline-flex size-5 items-center justify-center rounded-full border-2 border-background bg-primary font-semibold text-[10px] text-primary-foreground"
            >
              {unreadCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Notifications Preview"
        className="w-[min(20rem,calc(100vw-2rem))] gap-0 overflow-hidden rounded-xl border p-0 shadow-card"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">Notifications Preview</h2>
              <CapabilityBadge status="preview" />
            </div>
            <p className="mt-1 text-muted-foreground text-xs leading-5">
              Preview interactions are local and reset on reload.
            </p>
          </div>
          <Button
            className="h-auto shrink-0 px-0 py-1 text-xs"
            disabled={unreadCount === 0}
            onClick={() => setUnreadIds(new Set())}
            variant="link"
          >
            Mark all read
          </Button>
        </div>
        <ul aria-label="Preview notifications" className="divide-y divide-border">
          {PREVIEW_NOTIFICATIONS.map((notification) => {
            const isUnread = unreadIds.has(notification.id);

            return (
              <li key={notification.id}>
                <button
                  aria-label={`${notification.title}${isUnread ? ", unread" : ", read"}`}
                  className="flex min-h-20 w-full items-start gap-3 px-4 py-3 text-left outline-none transition-colors hover:bg-muted/60 focus-visible:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset"
                  onClick={() => markRead(notification.id)}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={cn("mt-1.5 size-2 shrink-0 rounded-full", isUnread ? "bg-primary" : "bg-transparent")}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-sm">{notification.title}</span>
                    <span className="mt-0.5 block text-muted-foreground text-xs leading-5">{notification.body}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-muted-foreground">{notification.time}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
