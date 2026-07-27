import { createContext, useContext } from "react";

import type { AuthMeResponse } from "@/lib/api/types";

export const AuthContext = createContext<AuthMeResponse | null>(null);

export function useAuth(): AuthMeResponse {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}
