import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type DataLedgerProps = {
  label: string;
  mode?: "list" | "table";
  header?: ReactNode;
  children?: ReactNode;
  empty?: ReactNode;
  error?: ReactNode;
  pagination?: ReactNode;
};

export type DataLedgerRowProps = {
  children: ReactNode;
};

export type DataLedgerCellProps = {
  label: string;
  children: ReactNode;
  primary?: boolean;
  hideAt?: "sm" | "md" | "lg";
};

export type DataLedgerActionProps = {
  children: ReactNode;
};

type LedgerMode = NonNullable<DataLedgerProps["mode"]>;
type InternalModeProps = { __mode?: LedgerMode };
type InternalRowProps = DataLedgerRowProps & InternalModeProps;
type InternalCellProps = DataLedgerCellProps & InternalModeProps;
type InternalActionProps = DataLedgerActionProps & InternalModeProps;

const LIST_HIDE_CLASS: Record<NonNullable<DataLedgerCellProps["hideAt"]>, string> = {
  sm: "hidden sm:flex",
  md: "hidden md:flex",
  lg: "hidden lg:flex",
};

const TABLE_HIDE_CLASS: Record<NonNullable<DataLedgerCellProps["hideAt"]>, string> = {
  sm: "hidden sm:table-cell",
  md: "hidden md:table-cell",
  lg: "hidden lg:table-cell",
};

function DataLedgerCell({ label, children, primary = false, hideAt, __mode = "list" }: InternalCellProps) {
  const isTable = __mode === "table";
  const Component = isTable ? "td" : "div";
  const hideClass = hideAt ? (isTable ? TABLE_HIDE_CLASS[hideAt] : LIST_HIDE_CLASS[hideAt]) : undefined;

  return (
    <Component
      className={cn(
        isTable
          ? "px-4 py-3 align-top text-sm"
          : "flex min-w-0 items-start justify-between gap-4 text-sm md:flex-col md:justify-start md:gap-1",
        primary ? "font-medium text-text-primary" : "text-text-secondary",
        hideClass,
      )}
      data-hide-at={hideAt}
      data-primary={primary || undefined}
      data-slot="data-ledger-cell"
    >
      <span
        className={cn("shrink-0 font-medium text-text-tertiary text-xs", isTable ? "md:hidden" : undefined)}
        data-slot="data-ledger-mobile-label"
      >
        {label}
      </span>
      <span className="min-w-0 break-words">{children}</span>
    </Component>
  );
}

function DataLedgerAction({ children, __mode = "list" }: InternalActionProps) {
  if (__mode === "table") {
    return (
      <td className="px-4 py-3 text-right align-top" data-slot="data-ledger-action">
        {children}
      </td>
    );
  }

  return (
    <div className="flex items-center justify-end" data-slot="data-ledger-action">
      {children}
    </div>
  );
}

function isCompoundChild(
  child: ReactNode,
): child is ReactElement<InternalCellProps | InternalActionProps, typeof DataLedgerCell | typeof DataLedgerAction> {
  return isValidElement(child) && (child.type === DataLedgerCell || child.type === DataLedgerAction);
}

function DataLedgerRow({ children, __mode = "list" }: InternalRowProps) {
  const rowChildren = Children.toArray(children)
    .filter(isCompoundChild)
    .map((child) => cloneElement(child, { __mode }));

  if (__mode === "table") {
    return (
      <tr className="grid border-border/70 border-b last:border-b-0 md:table-row" data-slot="data-ledger-row">
        {rowChildren}
      </tr>
    );
  }

  return (
    <li
      className="grid gap-4 border-border/70 border-b px-4 py-4 last:border-b-0 md:auto-cols-fr md:grid-flow-col md:items-center md:px-6"
      data-slot="data-ledger-row"
    >
      {rowChildren}
    </li>
  );
}

function isLedgerRow(child: ReactNode): child is ReactElement<InternalRowProps, typeof DataLedgerRow> {
  return isValidElement(child) && child.type === DataLedgerRow;
}

function rowsForMode(children: ReactNode, mode: LedgerMode) {
  return Children.toArray(children)
    .filter(isLedgerRow)
    .map((child) => cloneElement(child, { __mode: mode }));
}

function tableHeaders(children: ReactNode) {
  const firstRow = Children.toArray(children).find(isLedgerRow);

  if (!firstRow) return null;

  return Children.map(firstRow.props.children, (child) => {
    if (isValidElement<InternalCellProps>(child) && child.type === DataLedgerCell) {
      const hideAt = child.props.hideAt;
      return (
        <th
          className={cn(
            "px-4 py-3 text-left font-medium text-text-tertiary text-xs",
            hideAt ? TABLE_HIDE_CLASS[hideAt] : undefined,
          )}
          scope="col"
        >
          {child.props.label}
        </th>
      );
    }

    if (isValidElement(child) && child.type === DataLedgerAction) {
      return (
        <th className="px-4 py-3 text-right font-medium text-text-tertiary text-xs" scope="col">
          Actions
        </th>
      );
    }

    return null;
  });
}

function DataLedgerRoot({ label, mode = "list", header, children, empty, error, pagination }: DataLedgerProps) {
  const hasRows = Children.toArray(children).some(isLedgerRow);

  return (
    <div
      className="overflow-hidden rounded-lg border border-border/80 bg-surface shadow-raised"
      data-slot="data-ledger"
    >
      {header !== undefined ? (
        <header className="border-border/70 border-b px-4 py-4 sm:px-6" data-slot="data-ledger-header">
          {header}
        </header>
      ) : null}
      {error !== undefined ? (
        <div aria-label={`${label} error`} className="px-4 py-6 sm:px-6" role="alert">
          {error}
        </div>
      ) : hasRows ? (
        mode === "table" ? (
          <div className="overflow-x-auto">
            <table aria-label={label} className="block w-full border-collapse md:table">
              <thead className="hidden border-border/70 border-b bg-surface-subtle/50 md:table-header-group">
                <tr>{tableHeaders(children)}</tr>
              </thead>
              <tbody className="block md:table-row-group">{rowsForMode(children, mode)}</tbody>
            </table>
          </div>
        ) : (
          <ul aria-label={label}>{rowsForMode(children, mode)}</ul>
        )
      ) : empty !== undefined ? (
        <div aria-label={`${label} empty`} className="px-4 py-6 sm:px-6" role="status">
          {empty}
        </div>
      ) : null}
      {pagination !== undefined ? (
        <nav
          aria-label={`${label} pagination`}
          className="border-border/70 border-t px-4 py-4 sm:px-6"
          data-slot="data-ledger-pagination"
        >
          {pagination}
        </nav>
      ) : null}
    </div>
  );
}

type DataLedgerComponent = ((props: DataLedgerProps) => ReactElement) & {
  Row: (props: DataLedgerRowProps) => ReactElement;
  Cell: (props: DataLedgerCellProps) => ReactElement;
  Action: (props: DataLedgerActionProps) => ReactElement;
};

export const DataLedger: DataLedgerComponent = Object.assign(DataLedgerRoot, {
  Row: DataLedgerRow,
  Cell: DataLedgerCell,
  Action: DataLedgerAction,
});
