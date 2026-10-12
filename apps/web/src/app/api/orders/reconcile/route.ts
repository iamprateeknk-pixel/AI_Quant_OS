import {submitPaperPositionReconciliation} from "@/features/operator-session/server";

export async function POST(request:Request) {
  return submitPaperPositionReconciliation(request);
}
