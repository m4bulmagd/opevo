import { NextResponse } from "next/server";

import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

import { isClerkConfigured, shouldUseClerkMiddleware } from "@/lib/auth/clerk-config";

const isProtectedRoute = createRouteMatcher(["/dashboard(.*)"]);

const authProxy = clerkMiddleware(async (auth, request) => {
  if (isProtectedRoute(request)) {
    await auth.protect();
  }
});

export default shouldUseClerkMiddleware({
  nodeEnv: process.env.NODE_ENV,
  clerkConfigured: isClerkConfigured,
})
  ? authProxy
  : () => {
      return NextResponse.next();
    };

export const config = {
  matcher: ["/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|png|jpg|jpeg|gif|svg|ico|woff2?|ttf)).*)"],
};
