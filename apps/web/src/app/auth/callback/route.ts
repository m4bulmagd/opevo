import { type NextRequest, NextResponse } from "next/server";

import { completeAuthCallback } from "@/lib/auth/auth-callback";

function safeNextUrl(requestUrl: URL, value: string | null): URL {
  const fallback = new URL("/dashboard", requestUrl);
  if (!value?.startsWith("/") || value.startsWith("//")) {
    return fallback;
  }
  const destination = new URL(value, requestUrl);
  return destination.origin === requestUrl.origin ? destination : fallback;
}

export async function GET(request: NextRequest) {
  const redirectUrl = safeNextUrl(request.nextUrl, request.nextUrl.searchParams.get("next"));
  const outcome = await completeAuthCallback(request.nextUrl.searchParams.get("code"));
  if (outcome === "not-applicable" || outcome === "accepted") {
    return NextResponse.redirect(redirectUrl);
  }

  const signInUrl = request.nextUrl.clone();
  signInUrl.pathname = "/sign-in";
  signInUrl.search = "";
  signInUrl.searchParams.set("error", "confirmation_failed");
  return NextResponse.redirect(signInUrl);
}
