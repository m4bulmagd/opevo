import { authRouteProxy } from "@/lib/auth/route-protection";

export default authRouteProxy;

export const config = {
  matcher: ["/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|png|jpg|jpeg|gif|svg|ico|woff2?|ttf)).*)"],
};
