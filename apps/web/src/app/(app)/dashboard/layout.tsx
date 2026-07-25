import type { ReactNode } from "react";

import { cookies } from "next/headers";

import { AppSidebar } from "@/app/(app)/dashboard/_components/sidebar/app-sidebar";
import { LayoutControls } from "@/app/(app)/dashboard/_components/sidebar/layout-controls";
import { ThemeSwitcher } from "@/app/(app)/dashboard/_components/sidebar/theme-switcher";
import { AccountLifecycleBanner } from "@/components/account/account-lifecycle-banner";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { Separator } from "@/components/ui/separator";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { getAccount } from "@/lib/api/account";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { SIDEBAR_COLLAPSIBLE_VALUES, SIDEBAR_VARIANT_VALUES } from "@/lib/preferences/layout";
import { cn } from "@/lib/utils";
import { getPreference } from "@/server/server-actions";

export default async function AppLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Authentication is not configured"
        description="Add your Clerk keys to enable protected dashboard routes and backend data."
      />
    );
  }

  const [cookieStore, account, variant, collapsible] = await Promise.all([
    cookies(),
    getAccount(),
    getPreference("sidebar_variant", SIDEBAR_VARIANT_VALUES, "inset"),
    getPreference("sidebar_collapsible", SIDEBAR_COLLAPSIBLE_VALUES, "icon"),
  ]);
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
      <AppSidebar variant={variant} collapsible={collapsible} />
      <SidebarInset
        className={cn(
          "[html[data-content-layout=centered]_&>*]:mx-auto",
          "[html[data-content-layout=centered]_&>*]:w-full",
          "[html[data-content-layout=centered]_&>*]:max-w-screen-2xl",
          "peer-data-[variant=inset]:border",
        )}
      >
        <header
          className={cn(
            "flex h-12 shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12",
            "[html[data-navbar-style=sticky]_&]:sticky [html[data-navbar-style=sticky]_&]:top-0 [html[data-navbar-style=sticky]_&]:z-50 [html[data-navbar-style=sticky]_&]:overflow-hidden [html[data-navbar-style=sticky]_&]:rounded-t-[inherit] [html[data-navbar-style=sticky]_&]:bg-background/50 [html[data-navbar-style=sticky]_&]:backdrop-blur-md",
          )}
        >
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
              <LayoutControls />
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
