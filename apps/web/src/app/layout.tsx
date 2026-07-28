import type { ReactNode } from "react";

import type { Metadata } from "next";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { APP_CONFIG } from "@/config/app-config";
import { shouldWrapClerk } from "@/lib/auth/clerk-config";
import { PREFERENCE_DEFAULTS } from "@/lib/preferences/preferences-config";
import { ThemeBootScript } from "@/scripts/theme-boot";
import { PreferencesStoreProvider } from "@/stores/preferences/preferences-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: APP_CONFIG.meta.title,
  description: APP_CONFIG.meta.description,
};

export default async function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const { theme_mode } = PREFERENCE_DEFAULTS;
  const appShell = (
    <TooltipProvider>
      <PreferencesStoreProvider themeMode={theme_mode}>
        {children}
        <Toaster />
      </PreferencesStoreProvider>
    </TooltipProvider>
  );

  let bodyContent = appShell;

  if (shouldWrapClerk) {
    const { ClerkProvider } = await import("@clerk/nextjs");
    bodyContent = <ClerkProvider>{appShell}</ClerkProvider>;
  }

  return (
    <html lang="en" data-theme-mode={theme_mode} suppressHydrationWarning>
      <head>
        {/* Applies validated light/dark/system mode before first paint. */}
        <ThemeBootScript />
      </head>
      <body className="min-h-screen font-sans antialiased">{bodyContent}</body>
    </html>
  );
}
