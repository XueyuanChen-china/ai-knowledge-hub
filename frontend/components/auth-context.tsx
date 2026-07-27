import { AuthContext } from "@/components/auth-context-value";
import type { AuthMeResponse } from "@/lib/api/types";

export function AuthProvider({
  value,
  children,
}: {
  value: AuthMeResponse;
  children: React.ReactNode;
}) {
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
