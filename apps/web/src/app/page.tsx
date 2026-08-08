import { OpevoLandingPage } from "@/components/landing/opevo-landing-page";
import { getServerSessionState } from "@/lib/auth/server-session";

export default async function Page() {
  const session = await getServerSessionState();

  return <OpevoLandingPage isAuthenticated={session.isAuthenticated} />;
}
