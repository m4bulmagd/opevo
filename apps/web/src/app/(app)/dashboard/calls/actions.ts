"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { deleteCall } from "@/lib/api/calls";

export type DeleteCallActionResult = {
  status: "error";
  message: string;
};

export async function deleteCallAction(callId: string): Promise<DeleteCallActionResult> {
  try {
    await deleteCall(callId);
  } catch {
    return {
      status: "error",
      message: "Opevo could not remove this call right now. Try again.",
    };
  }

  revalidatePath("/dashboard/calls");
  revalidatePath("/dashboard");
  redirect("/dashboard/calls");
}
