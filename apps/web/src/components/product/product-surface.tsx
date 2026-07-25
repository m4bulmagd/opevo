import { type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

export type ProductSurfaceProps = {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  as?: "section" | "article" | "div";
  tone?: "default" | "subtle" | "danger";
};

const TONE_CLASS: Record<NonNullable<ProductSurfaceProps["tone"]>, string> = {
  default: "border-border/80 bg-surface shadow-raised",
  subtle: "border-border/70 bg-surface-subtle/50",
  danger: "border-destructive/30 bg-destructive-subtle",
};

export function ProductSurface({
  title,
  description,
  action,
  children,
  footer,
  as = "section",
  tone = "default",
}: ProductSurfaceProps) {
  const titleId = useId();
  const Component = as;
  const hasHeader = title !== undefined || description !== undefined || action !== undefined;

  return (
    <Component
      aria-labelledby={title !== undefined ? titleId : undefined}
      className={cn("overflow-hidden rounded-lg border text-text-primary", TONE_CLASS[tone])}
      data-slot="product-surface"
      data-tone={tone}
    >
      {hasHeader ? (
        <header className="flex flex-col gap-4 px-4 pt-4 sm:flex-row sm:items-start sm:justify-between sm:px-6 sm:pt-6">
          <div className="flex min-w-0 flex-col gap-1.5">
            {title !== undefined ? (
              <h2 className="font-semibold text-base tracking-tight sm:text-lg" id={titleId}>
                {title}
              </h2>
            ) : null}
            {description !== undefined ? (
              <div className="text-sm text-text-secondary leading-relaxed">{description}</div>
            ) : null}
          </div>
          {action !== undefined ? (
            <div className="flex shrink-0 items-center" data-slot="product-surface-action">
              {action}
            </div>
          ) : null}
        </header>
      ) : null}
      <div className="px-4 py-4 sm:px-6 sm:py-6" data-slot="product-surface-content">
        {children}
      </div>
      {footer !== undefined ? (
        <footer
          className="border-border/70 border-t bg-surface-subtle/40 px-4 py-4 text-sm text-text-secondary sm:px-6"
          data-slot="product-surface-footer"
        >
          {footer}
        </footer>
      ) : null}
    </Component>
  );
}
