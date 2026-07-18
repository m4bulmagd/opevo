import { NextResponse } from "next/server";

import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

import { shouldWrapClerk } from "@/lib/auth/clerk-config";

const isProtectedRoute = createRouteMatcher(["/activate(.*)", "/dashboard(.*)"]);

const authProxy = clerkMiddleware(async (auth, request) => {
  if (isProtectedRoute(request)) {
    await auth.protect();
  }
});

export default shouldWrapClerk
  ? authProxy
  : () => {
      return NextResponse.next();
    };

export const config = {
  matcher: ["/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|png|jpg|jpeg|gif|svg|ico|woff2?|ttf)).*)"],
};
