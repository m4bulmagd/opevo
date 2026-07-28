import { Badge } from "@/components/ui/badge";
import type { CapabilityStatus } from "@/lib/types/capability";
import { cn } from "@/lib/utils";

const CAPABILITY_LABEL: Record<CapabilityStatus, string> = {
  live: "Live",
  preview: "Preview",
  unavailable: "Unavailable",
};

const CAPABILITY_CLASS: Record<CapabilityStatus, string> = {
  live: "border-success/20 bg-success/10 text-success",
  preview: "border-primary/20 bg-primary-soft text-accent-foreground",
  unavailable: "border-border bg-muted text-muted-foreground",
};

export function CapabilityBadge({ className, status }: { className?: string; status: CapabilityStatus }) {
  return (
    <Badge className={cn(CAPABILITY_CLASS[status], className)} data-capability-status={status} variant="outline">
      {CAPABILITY_LABEL[status]}
    </Badge>
  );
}
