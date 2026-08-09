import type { ReactNode } from "react";

import type { Metadata, Viewport } from "next";

import { AuthProviderRoot } from "@/components/auth/auth-provider-root";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { APP_CONFIG } from "@/config/app-config";
import { PREFERENCE_DEFAULTS } from "@/lib/preferences/preferences-config";
import { ThemeBootScript } from "@/scripts/theme-boot";
import { PreferencesStoreProvider } from "@/stores/preferences/preferences-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: APP_CONFIG.meta.title,
  description: APP_CONFIG.meta.description,
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8f9f6" },
    { media: "(prefers-color-scheme: dark)", color: "#101511" },
  ],
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

  return (
    <html lang="en" data-theme-mode={theme_mode} suppressHydrationWarning>
      <head>
        {/* Applies validated light/dark/system mode before first paint. */}
        <ThemeBootScript />
      </head>
      <body className="min-h-screen font-sans antialiased">
        <AuthProviderRoot>{appShell}</AuthProviderRoot>
      </body>
    </html>
  );
}
