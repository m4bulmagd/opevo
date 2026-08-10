import type { ReactNode } from "react";

import { authProvider } from "@/lib/auth/auth-config";
import { ClerkProviderRoot } from "@/lib/auth/providers/clerk/provider-root";

export function AuthProviderRoot({ children }: Readonly<{ children: ReactNode }>) {
  return authProvider === "clerk" ? <ClerkProviderRoot>{children}</ClerkProviderRoot> : children;
}
