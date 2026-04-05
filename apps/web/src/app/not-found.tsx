import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="text-muted-foreground text-sm">404</p>
      <h1 className="font-semibold text-2xl">Page not found</h1>
      <p className="max-w-md text-muted-foreground">The page you requested does not exist or may have been moved.</p>
      <Link href="/" className="text-primary text-sm underline underline-offset-4">
        Return home
      </Link>
    </main>
  );
}
