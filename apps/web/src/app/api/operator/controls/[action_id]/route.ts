import { controlReceipt } from "@/features/operator-session/server";

export async function GET(_request: Request, context: { params: Promise<{ action_id: string }> }) {
  return controlReceipt((await context.params).action_id);
}
