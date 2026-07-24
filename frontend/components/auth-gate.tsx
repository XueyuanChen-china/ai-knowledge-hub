import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Center, Loader } from "@mantine/core";

import {
  clearAuthSession,
  getAuthToken,
  getCurrentUser,
} from "@/lib/api/client";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [checking, setChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;

    async function validateSession() {
      if (!getAuthToken()) {
        if (active) {
          setAuthenticated(false);
          setChecking(false);
        }
        return;
      }

      try {
        await getCurrentUser();
        if (active) {
          setAuthenticated(true);
        }
      } catch {
        clearAuthSession();
        if (active) {
          setAuthenticated(false);
        }
      } finally {
        if (active) {
          setChecking(false);
        }
      }
    }

    void validateSession();

    function handleExpired() {
      setAuthenticated(false);
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

  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return children;
}
