"use server";

import { revalidatePath } from "next/cache";

import { archiveCall } from "@/lib/api/calls";

export async function archiveCallAction(callId: string) {
  await archiveCall(callId);
  revalidatePath("/dashboard/calls");
  revalidatePath("/dashboard");
}
