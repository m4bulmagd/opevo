import Link from "next/link";

import { PhoneCall } from "lucide-react";

import { WorkspaceNavigation } from "@/components/workspace/workspace-navigation";

type CommandRailProps = {
  agentName: string;
  runtimeState: "Attention required" | "Deactivating" | "Enabled" | "Inactive" | "Paused";
};

export function CommandRail({ agentName, runtimeState }: CommandRailProps) {
  return (
    <aside aria-label="Workspace sidebar" className="hidden w-64 shrink-0 lg:block">
      <div
        className="sticky top-4 flex h-[calc(100svh-2rem)] flex-col gap-6 overflow-hidden rounded-2xl border border-sidebar-border bg-sidebar p-4 text-sidebar-foreground shadow-card"
        data-slot="workspace-sidebar-panel"
      >
        <Link
          aria-label="Presvo overview"
          className="flex min-h-11 items-center gap-3 rounded-lg px-2 py-1.5 outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/50"
          href="/dashboard"
          prefetch={false}
        >
          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
            <PhoneCall aria-hidden="true" className="size-4" />
          </span>
          <span className="min-w-0">
            <span className="block truncate font-semibold text-sm tracking-tight">Presvo</span>
            <span className="block truncate text-muted-foreground text-xs">AI Call Assistant</span>
          </span>
        </Link>
        <WorkspaceNavigation agentName={agentName} variant="rail" />
        <fieldset
          aria-label={`Agent runtime: ${agentName}, ${runtimeState}`}
          className="min-w-0 rounded-xl border border-sidebar-border bg-card px-3 py-2.5"
        >
          <p className="truncate font-medium text-sm" title={agentName}>
            {agentName}
          </p>
          <p className="mt-1 text-muted-foreground text-xs">{runtimeState}</p>
        </fieldset>
      </div>
    </aside>
  );
}
