import { Children, type ReactNode } from "react";

import { AnimatedStatusBadge, type StatusTone as AnimatedStatusTone } from "@/components/motion/animated-status-badge";
import { cn } from "@/lib/utils";

export type StatusSurfaceTone = AnimatedStatusTone;

export type StatusSurfaceProps = {
  tone: StatusSurfaceTone;
  label: string;
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
};

const TONE_CLASS: Record<StatusSurfaceTone, string> = {
  neutral: "border-border/80 bg-surface",
  live: "border-success/30 bg-success-subtle/40",
  ready: "border-primary/30 bg-primary/5",
  processing: "border-primary/30 bg-primary/5",
  paused: "border-border bg-surface-subtle/60",
  warning: "border-warning/30 bg-warning-subtle/50",
  attention: "border-warning/30 bg-warning-subtle/50",
  inactive: "border-border bg-surface-subtle/60",
};

function hasRenderableIcon(icon: ReactNode) {
  return Children.toArray(icon).some((child) => typeof child !== "string" || child.length > 0);
}

export function StatusSurface({ tone, label, title, description, icon, action, children }: StatusSurfaceProps) {
  const statusIcon = hasRenderableIcon(icon) ? (
    icon
  ) : (
    <span aria-hidden className="size-1.5 rounded-full bg-current" data-status-marker />
  );

  return (
    <section
      aria-label={label}
      className={cn(
        "flex flex-col gap-4 rounded-lg border px-4 py-4 text-text-primary shadow-raised sm:px-6 sm:py-6",
        TONE_CLASS[tone],
      )}
      data-slot="status-surface"
      data-tone={tone}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-3">
          <AnimatedStatusBadge icon={statusIcon} label={label} tone={tone} />
          <div className="flex flex-col gap-1.5">
            <h2 className="font-semibold text-lg tracking-tight">{title}</h2>
            {description !== undefined ? (
              <div className="text-sm text-text-secondary leading-relaxed">{description}</div>
            ) : null}
          </div>
        </div>
        {action !== undefined ? (
          <div className="flex shrink-0 items-center" data-slot="status-surface-action">
            {action}
          </div>
        ) : null}
      </div>
      {children !== undefined ? (
        <div className="text-sm text-text-secondary leading-relaxed" data-slot="status-surface-content">
          {children}
        </div>
      ) : null}
    </section>
  );
}
