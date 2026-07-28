import { Skeleton } from "@/components/ui/skeleton";

const PROGRESS_SEGMENTS = ["business", "receptionist", "number", "forwarding", "launch"] as const;

export default function ActivationLoading() {
  return (
    <main
      aria-label="Loading activation"
      className="mx-auto flex min-h-[calc(100svh-4rem)] w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 sm:py-12"
      id="activation-content"
      role="status"
    >
      <span className="sr-only">Loading activation</span>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-20" />
        <div className="grid grid-cols-5 gap-1.5 sm:gap-2">
          {PROGRESS_SEGMENTS.map((segment) => (
            <div className="flex flex-col gap-2" key={segment}>
              <Skeleton className="h-1.5 w-full rounded-full" />
              <Skeleton className="mx-auto h-3 w-4/5" />
            </div>
          ))}
        </div>
      </div>
      <div
        className="rounded-2xl border border-border bg-card p-5 shadow-card sm:p-7"
        data-slot="activation-loading-card"
      >
        <div className="flex flex-col gap-3 border-border border-b pb-5">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-8 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
        <div className="grid gap-5 pt-5 sm:grid-cols-2">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-28 w-full sm:col-span-2" />
          <Skeleton className="h-11 w-32 justify-self-end sm:col-span-2" />
        </div>
      </div>
    </main>
  );
}
