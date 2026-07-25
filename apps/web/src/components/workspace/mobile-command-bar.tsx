import { PresvoMotionProvider } from "@/components/motion/presvo-motion-provider";
import { WorkspaceNavigation } from "@/components/workspace/workspace-navigation";

export function MobileCommandBar({ agentName }: { agentName: string }) {
  return (
    <PresvoMotionProvider>
      <WorkspaceNavigation agentName={agentName} variant="mobile" />
    </PresvoMotionProvider>
  );
}
