import { ShieldAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { CLERK_REQUIRED_ENV_VARS } from "@/lib/auth/clerk-config";

type ClerkSetupNoticeProps = {
  title: string;
  description: string;
};

export function ClerkSetupNotice({ title, description }: ClerkSetupNoticeProps) {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <Empty className="max-w-2xl border border-dashed bg-card text-card-foreground">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ShieldAlert />
          </EmptyMedia>
          <EmptyTitle>{title}</EmptyTitle>
          <EmptyDescription>{description}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Alert>
            <ShieldAlert />
            <AlertTitle>Required environment variables</AlertTitle>
            <AlertDescription className="flex flex-wrap justify-center gap-2">
              {CLERK_REQUIRED_ENV_VARS.map((name) => (
                <code key={name} className="rounded bg-muted px-2 py-1 font-mono text-foreground text-xs">
                  {name}
                </code>
              ))}
            </AlertDescription>
          </Alert>
        </EmptyContent>
      </Empty>
    </main>
  );
}
