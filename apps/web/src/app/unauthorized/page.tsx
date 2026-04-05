import Link from "next/link";

export default function UnauthorizedPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="text-muted-foreground text-sm">401</p>
      <h1 className="font-semibold text-2xl">Unauthorized</h1>
      <p className="max-w-md text-muted-foreground">Sign in with an account that has access to this workspace.</p>
      <Link href="/sign-in" className="text-primary text-sm underline underline-offset-4">
        Go to sign in
      </Link>
    </main>
  );
}
