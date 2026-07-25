import { type ReactNode, useId } from "react";

export type SettingsSectionProps = {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  validation?: ReactNode;
  action?: ReactNode;
  status?: ReactNode;
};

export function SettingsSection({ title, description, children, validation, action, status }: SettingsSectionProps) {
  const titleId = useId();
  const supportingCopyId = useId();

  return (
    <section
      aria-describedby={description !== undefined ? supportingCopyId : undefined}
      aria-labelledby={titleId}
      className="flex flex-col gap-6 border-border/80 border-b py-6 first:pt-0 last:border-b-0 last:pb-0"
      data-slot="settings-section"
    >
      <header className="flex max-w-2xl flex-col gap-2">
        <h2 className="font-semibold text-lg text-text-primary tracking-tight" id={titleId}>
          {title}
        </h2>
        {description !== undefined ? (
          <div className="text-sm text-text-secondary leading-relaxed" id={supportingCopyId}>
            {description}
          </div>
        ) : null}
      </header>
      <div className="flex flex-col gap-6" data-slot="settings-section-controls">
        {children}
      </div>
      {validation !== undefined ? (
        <div className="text-destructive text-sm" data-slot="settings-section-validation" role="alert">
          {validation}
        </div>
      ) : null}
      {status !== undefined || action !== undefined ? (
        <footer className="flex flex-col gap-4 border-border/70 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
          {status !== undefined ? (
            <div className="text-sm text-text-secondary" data-slot="settings-section-status" role="status">
              {status}
            </div>
          ) : null}
          {action !== undefined ? (
            <div className="flex items-center justify-end sm:ml-auto" data-slot="settings-section-action">
              {action}
            </div>
          ) : null}
        </footer>
      ) : null}
    </section>
  );
}
