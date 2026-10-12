import {submitMonitoringAcknowledgement} from "@/features/operator-session/server";

export async function POST(request:Request) {
 return submitMonitoringAcknowledgement(request);
}
