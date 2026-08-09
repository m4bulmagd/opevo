import Link from "next/link";

import { KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";

export function SupabaseSecurityButton() {
  return (
    <Button asChild className="min-h-11" variant="outline">
      <Link href="/update-password">
        <KeyRound aria-hidden data-icon="inline-start" />
        Change password
      </Link>
    </Button>
  );
}
