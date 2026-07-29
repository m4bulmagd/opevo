import { ReceiptText } from "lucide-react";

import { DataLedger } from "@/components/product/data-ledger";
import { ProductSurface } from "@/components/product/product-surface";
import { Badge } from "@/components/ui/badge";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { formatCallTime, toTitleCase } from "@/lib/formatters";
import type { UsageLedgerEntry } from "@/lib/types/billing";

function EmptyUsageLedger() {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <ReceiptText />
        </EmptyMedia>
        <EmptyTitle>No billing activity yet</EmptyTitle>
        <EmptyDescription>Usage ledger events will appear here after the first plan or call event.</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}

export function UsageLedgerList({ entries }: { entries: UsageLedgerEntry[] }) {
  return (
    <ProductSurface description="Recent minute adjustments and usage events for this customer." title="Usage history">
      <DataLedger empty={<EmptyUsageLedger />} label="Usage ledger">
        {entries.map((entry) => (
          <DataLedger.Row key={entry.id}>
            <DataLedger.Cell label="Event" primary>
              {toTitleCase(entry.event_type)}
            </DataLedger.Cell>
            <DataLedger.Cell label="Date">
              <time data-visual-billing-date="true" dateTime={entry.created_at}>
                {formatCallTime(entry.created_at)}
              </time>
            </DataLedger.Cell>
            <DataLedger.Cell label="Change">
              <Badge variant="outline">
                {entry.minutes_delta >= 0 ? `+${entry.minutes_delta}` : entry.minutes_delta}
              </Badge>
            </DataLedger.Cell>
            <DataLedger.Cell label="Balance">{entry.balance_after ?? 0}</DataLedger.Cell>
          </DataLedger.Row>
        ))}
      </DataLedger>
    </ProductSurface>
  );
}
