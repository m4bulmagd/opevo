import { Figtree, Geist_Mono, Inter } from "next/font/google";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-figtree",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
});

/**
 * Public and activation routes inherit Inter. Figtree is deliberately omitted
 * and must be scoped by the authenticated WorkspaceShell.
 */
export const publicFontVariables = `${inter.variable} ${geistMono.variable}`;

/** Attach this class to the authenticated product wrapper only. */
export const authenticatedFontVariable = figtree.variable;
