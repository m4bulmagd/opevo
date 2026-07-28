import { PresvoLandingPage } from "@/components/landing/presvo-landing-page";
import { getServerSessionState } from "@/lib/auth/server-session";

export default async function Page() {
  const session = await getServerSessionState();

  return <PresvoLandingPage isAuthenticated={session.isAuthenticated} />;
}
