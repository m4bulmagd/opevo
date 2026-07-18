import { Skeleton } from "@/components/ui/skeleton";

export default function ActivationLoading() {
  return (
    <main
      id="activation-content"
      role="status"
      aria-label="Loading activation"
      className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 px-5 py-8 sm:px-8 sm:py-12"
    >
      <span className="sr-only">Loading activation</span>
      <div className="flex flex-col gap-4">
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-10 w-full" />
      </div>
      <div className="flex max-w-3xl flex-col gap-4">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-10 w-3/4" />
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-4/5" />
      </div>
    </main>
  );
}
