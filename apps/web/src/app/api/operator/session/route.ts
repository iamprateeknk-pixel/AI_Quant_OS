import { sessionLogin, sessionLogout, sessionStatus } from "@/features/operator-session/server";

export const GET = sessionStatus;
export const POST = sessionLogin;
export const DELETE = sessionLogout;
