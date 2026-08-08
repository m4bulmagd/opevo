import type { Metadata } from "next";

import { LiveCallPreview } from "@/components/live-call/live-call-preview";

export const metadata: Metadata = {
  title: "Live call preview — Opevo",
  description: "Explore a local-only preview of the Opevo live call workspace.",
};

export default function LiveCallPreviewPage() {
  return <LiveCallPreview />;
}
