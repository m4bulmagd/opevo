import { PhoneCall } from "lucide-react";

import { formatPhoneNumber } from "@/lib/formatters";

export type WorkspaceCallerIdentity = Readonly<{
  contactName: string | null;
  phoneNumber: string | null;
}>;

type WorkspaceCallerStatusProps = {
  agentName: string;
  caller: WorkspaceCallerIdentity | null;
};

function initialsFor(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function WorkspaceCallerStatus({ agentName, caller }: WorkspaceCallerStatusProps) {
  const contactName = caller?.contactName?.trim() || null;
  const phoneNumber = caller?.phoneNumber?.trim() || null;
  const primary = caller
    ? (contactName ?? (phoneNumber ? formatPhoneNumber(phoneNumber) : "Unknown caller"))
    : "No active call";
  const secondary = caller ? `${agentName} is answering this call` : `${agentName} is ready`;

  return (
    <div
      className="hidden min-w-0 shrink-0 items-center gap-3 xl:flex"
      data-header-item="caller-status"
      data-slot="workspace-caller-status"
    >
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-full bg-primary-soft font-semibold text-accent-foreground text-xs"
      >
        {contactName ? initialsFor(contactName) : <PhoneCall className="size-4" data-testid="caller-status-icon" />}
      </span>
      <span className="min-w-0 max-w-52">
        <span className="block truncate font-semibold text-sm" title={primary}>
          {primary}
        </span>
        <span className="block truncate text-muted-foreground text-xs" title={secondary}>
          {secondary}
        </span>
      </span>
    </div>
  );
}
