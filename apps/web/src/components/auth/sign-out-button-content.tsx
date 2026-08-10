import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";

export type SignOutVariant = "activation" | "mobile" | "workspace";

type SignOutButtonContentProps = Readonly<{
  variant: SignOutVariant;
  disabled?: boolean;
  onClick?: () => void;
}>;

export function SignOutButtonContent({ disabled, onClick, variant }: SignOutButtonContentProps) {
  if (variant === "activation") {
    return (
      <Button aria-label="Sign out" disabled={disabled} onClick={onClick} size="sm" variant="ghost">
        <LogOut data-icon="inline-start" />
        <span className="hidden sm:inline">Sign out</span>
      </Button>
    );
  }

  if (variant === "mobile") {
    return (
      <Button
        aria-label="Sign out"
        className="min-h-11 w-full justify-start px-3"
        disabled={disabled}
        onClick={onClick}
        variant="ghost"
      >
        <LogOut aria-hidden="true" data-icon="inline-start" />
        Sign out
      </Button>
    );
  }

  return (
    <Button aria-label="Sign out" className="size-11" disabled={disabled} onClick={onClick} size="icon" variant="ghost">
      <LogOut aria-hidden="true" />
    </Button>
  );
}
