import type { ReactNode } from "react";

export type PageIntroProps = {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  dynamicContext?: boolean;
};

export function PageIntro({ eyebrow, title, description, action, dynamicContext = false }: PageIntroProps) {
  return (
    <header
      className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between"
      data-dynamic-context={dynamicContext || undefined}
      data-slot="page-intro"
    >
      <div className="flex min-w-0 flex-col gap-3">
        {eyebrow !== undefined ? (
          <div
            className="font-medium text-text-tertiary text-xs uppercase tracking-widest data-[dynamic-context=true]:normal-case data-[dynamic-context=true]:tracking-normal"
            data-dynamic-context={dynamicContext || undefined}
            data-slot="page-intro-eyebrow"
          >
            {eyebrow}
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <h1 className="font-semibold text-text-primary text-xl tracking-tight sm:text-2xl">{title}</h1>
          {description !== undefined ? (
            <div className="max-w-2xl text-sm text-text-secondary leading-relaxed sm:text-base">{description}</div>
          ) : null}
        </div>
      </div>
      {action !== undefined ? (
        <div className="flex shrink-0 items-center" data-slot="page-intro-action">
          {action}
        </div>
      ) : null}
    </header>
  );
}
