// Adapted from beUI's shared motion tokens:
// https://beui.dev/components/motion/animated-badge
// Retrieved through the connected beUI registry; non-bouncy Opevo subset only.

export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
export const EASE_DRAWER = [0.32, 0.72, 0, 1] as const;
export const SPRING_LAYOUT = {
  type: "spring",
  stiffness: 360,
  damping: 32,
  mass: 0.6,
} as const;
