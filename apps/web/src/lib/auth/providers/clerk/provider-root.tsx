import type { ReactNode } from "react";

import { ClerkProvider } from "@clerk/nextjs";

export function ClerkProviderRoot({ children }: Readonly<{ children: ReactNode }>) {
  return <ClerkProvider>{children}</ClerkProvider>;
}
