import type { ReactNode } from "react";

import { cookies } from "next/headers";

import { AppSidebar } from "@/app/(app)/dashboard/_components/sidebar/app-sidebar";
import { ThemeSwitcher } from "@/app/(app)/dashboard/_components/sidebar/theme-switcher";
import { AccountLifecycleBanner } from "@/components/account/account-lifecycle-banner";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { getAccount } from "@/lib/api/account";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function AppLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Authentication is not configured"
        description="Add your Clerk keys to enable protected dashboard routes and backend data."
      />
    );
  }

  const [cookieStore, account] = await Promise.all([cookies(), getAccount()]);
  const defaultOpen = cookieStore.get("sidebar_state")?.value !== "false";

  return (
    <SidebarProvider
      defaultOpen={defaultOpen}
      style={
        {
          "--sidebar-width": "calc(var(--spacing) * 68)",
        } as React.CSSProperties
      }
    >
      <AppSidebar variant="inset" collapsible="icon" />
      <SidebarInset className="peer-data-[variant=inset]:border">
        <header className="sticky top-0 z-50 flex h-12 shrink-0 items-center gap-2 overflow-hidden rounded-t-[inherit] border-b bg-background group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
          <div className="flex w-full items-center justify-between px-4 lg:px-6">
            <div className="flex items-center gap-1 lg:gap-2">
              <SidebarTrigger className="-ml-1" />
              <Separator
                orientation="vertical"
                className="mx-2 data-[orientation=vertical]:h-4 data-[orientation=vertical]:self-center"
              />
              <span className="font-medium text-muted-foreground text-sm">Customer dashboard</span>
            </div>
            <div className="flex items-center gap-2">
              <ThemeSwitcher />
            </div>
          </div>
        </header>
        {account.status === "active" ? null : (
          <div className="px-4 pt-4 md:px-6 md:pt-6">
            <AccountLifecycleBanner account={account} />
          </div>
        )}
        <div className="h-full p-4 md:p-6">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}
