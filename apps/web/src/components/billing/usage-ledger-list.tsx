import { ReceiptText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, toTitleCase } from "@/lib/formatters";
import type { UsageLedgerEntry } from "@/lib/types/billing";

export function UsageLedgerList({ entries }: { entries: UsageLedgerEntry[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage ledger</CardTitle>
        <CardDescription>Recent minute adjustments and usage events for this customer.</CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ReceiptText />
              </EmptyMedia>
              <EmptyTitle>No billing activity yet</EmptyTitle>
              <EmptyDescription>
                Usage ledger events will appear here after the first plan or call event.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {entries.map((entry) => (
              <div key={entry.id} className="flex items-center justify-between gap-3 rounded-xl border px-4 py-4">
                <div className="flex flex-col gap-1">
                  <span className="font-medium">{toTitleCase(entry.event_type)}</span>
                  <span className="text-muted-foreground text-xs">{formatCallTime(entry.created_at)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">
                    {entry.minutes_delta >= 0 ? `+${entry.minutes_delta}` : entry.minutes_delta}
                  </Badge>
                  <span className="text-muted-foreground text-xs">Balance {entry.balance_after ?? 0}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
