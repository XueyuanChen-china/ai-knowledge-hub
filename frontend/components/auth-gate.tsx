import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Center, Loader } from "@mantine/core";

import { AuthProvider } from "@/components/auth-context";
import {
  clearAuthSession,
  getAuthToken,
  getCurrentUser,
} from "@/lib/api/client";
import type { AuthMeResponse } from "@/lib/api/types";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [checking, setChecking] = useState(true);
  const [principal, setPrincipal] = useState<AuthMeResponse | null>(null);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;

    async function validateSession() {
      if (!getAuthToken()) {
        if (active) {
          setPrincipal(null);
          setChecking(false);
        }
        return;
      }

      try {
        const currentPrincipal = await getCurrentUser();
        if (active) {
          setPrincipal(currentPrincipal);
        }
      } catch {
        clearAuthSession();
        if (active) {
          setPrincipal(null);
        }
      } finally {
        if (active) {
          setChecking(false);
        }
      }
    }

    void validateSession();

    function handleExpired() {
      setPrincipal(null);
      navigate("/login", { replace: true, state: { from: location.pathname } });
    }

    window.addEventListener("ai-knowledge-hub.auth-expired", handleExpired);
    return () => {
      active = false;
      window.removeEventListener(
        "ai-knowledge-hub.auth-expired",
        handleExpired,
      );
    };
  }, [location.pathname, navigate]);

  if (checking) {
    return (
      <Center mih="100vh">
        <Loader size="sm" />
      </Center>
    );
  }

  if (!principal) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <AuthProvider value={principal}>{children}</AuthProvider>;
}
