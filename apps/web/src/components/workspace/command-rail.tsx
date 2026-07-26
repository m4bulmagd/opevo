import Link from "next/link";

import { PhoneCall } from "lucide-react";

import { PresvoMotionProvider } from "@/components/motion/presvo-motion-provider";
import { WorkspaceNavigation } from "@/components/workspace/workspace-navigation";

type CommandRailProps = {
  agentName: string;
  runtimeState: "Attention required" | "Deactivating" | "Enabled" | "Inactive" | "Paused";
};

export function CommandRail({ agentName, runtimeState }: CommandRailProps) {
  return (
    <aside
      aria-label="Workspace command rail"
      className="fixed inset-y-0 left-0 z-30 hidden w-18 flex-col overflow-hidden border-sidebar-border border-r bg-sidebar text-sidebar-foreground md:flex md:w-18 lg:w-64"
    >
      <div className="flex min-h-20 items-center px-3 lg:px-5">
        <Link
          aria-label="Presvo overview"
          className="flex min-h-11 min-w-11 items-center gap-3 overflow-hidden rounded-md outline-none focus-visible:ring-3 focus-visible:ring-sidebar-ring/60"
          href="/dashboard"
          prefetch={false}
        >
          <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <PhoneCall aria-hidden="true" className="size-5" />
          </span>
          <span className="hidden truncate font-semibold tracking-tight lg:block">Presvo</span>
        </Link>
      </div>
      <PresvoMotionProvider>
        <WorkspaceNavigation agentName={agentName} variant="rail" />
      </PresvoMotionProvider>
      <fieldset
        aria-label={`Agent runtime: ${agentName}, ${runtimeState}`}
        className="mx-4 hidden min-w-0 border-0 border-sidebar-border border-t py-5 lg:block"
      >
        <p className="truncate font-medium text-sidebar-foreground text-sm" title={agentName}>
          {agentName}
        </p>
        <p className="mt-1 text-sidebar-foreground/70 text-xs">{runtimeState}</p>
      </fieldset>
    </aside>
  );
}
