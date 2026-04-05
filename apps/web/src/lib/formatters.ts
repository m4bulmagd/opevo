import { format, formatDistanceToNowStrict } from "date-fns";

export function formatCallTime(value: string | null) {
  if (!value) return "No timestamp";
  return format(new Date(value), "MMM d, HH:mm");
}

export function formatRelativeTime(value: string | null) {
  if (!value) return "No recent activity";
  return `${formatDistanceToNowStrict(new Date(value), { addSuffix: true })}`;
}

export function formatDuration(seconds: number | null) {
  if (seconds == null) return "Unknown duration";

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;

  if (minutes === 0) return `${remainingSeconds}s`;
  if (remainingSeconds === 0) return `${minutes}m`;
  return `${minutes}m ${remainingSeconds}s`;
}

export function formatMinutes(value: number | null) {
  if (value == null) return "0 min";
  return `${value} min`;
}

export function formatPhoneNumber(value: string | null) {
  return value ?? "Unknown caller";
}

export function toTitleCase(value: string | null | undefined) {
  if (!value) return "Unassigned";

  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
