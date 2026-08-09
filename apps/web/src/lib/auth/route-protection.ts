import { NextResponse } from "next/server";

import { authProvider } from "@/lib/auth/auth-config";
import { clerkAuthProxy } from "@/lib/auth/providers/clerk/proxy";
import { supabaseAuthProxy } from "@/lib/auth/providers/supabase/proxy";

export const authRouteProxy =
  authProvider === "clerk"
    ? clerkAuthProxy
    : authProvider === "supabase"
      ? supabaseAuthProxy
      : () => NextResponse.next();
