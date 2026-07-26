import Link from "next/link";

import { ArrowUpRight, Flag } from "lucide-react";

import { ProductSurface } from "@/components/product/product-surface";
import { formatPhoneNumber } from "@/lib/formatters";
import type { CallHistoryListItem } from "@/lib/types/calls";

export function AttentionSurface({ calls }: { calls: CallHistoryListItem[] }) {
  const flaggedCalls = calls.filter((call) => call.follow_up_required === true);

  return (
    <ProductSurface
      description="Recent conversations that were flagged for your follow-up."
      title="Needs attention"
      tone="subtle"
    >
      {flaggedCalls.length === 0 ? (
        <div className="flex items-start gap-3 text-sm text-text-secondary">
          <Flag aria-hidden className="mt-0.5 size-4 shrink-0" />
          <p>No recent calls are flagged for follow-up.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border/70">
          {flaggedCalls.map((call) => (
            <li key={call.id}>
              <Link
                className="group flex min-h-14 items-center justify-between gap-4 rounded-sm py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                href={`/dashboard/calls/${call.id}`}
              >
                <span className="min-w-0">
                  <span className="block font-semibold text-sm text-text-primary">
                    {formatPhoneNumber(call.caller_number)}
                  </span>
                  <span className="mt-0.5 block truncate text-sm text-text-secondary">
                    {call.caller_intent ?? "Review this conversation"}
                  </span>
                </span>
                <ArrowUpRight
                  aria-hidden
                  className="size-4 shrink-0 text-text-tertiary transition-colors group-hover:text-primary"
                />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </ProductSurface>
  );
}
