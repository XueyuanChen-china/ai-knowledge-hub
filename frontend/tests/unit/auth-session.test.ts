import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAuthSession,
  getAuthToken,
  getKnowledgeBases,
  setAuthToken,
} from "@/lib/api/client";

describe("auth session and API expiry handling", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("stores credentials only in the current tab session", () => {
    setAuthToken("test-token");
    expect(getAuthToken()).toBe("test-token");
    clearAuthSession();
    expect(getAuthToken()).toBeNull();
  });

  it("clears the token and emits auth-expired on a 401", async () => {
    setAuthToken("expired-token");
    const expired = vi.fn();
    window.addEventListener("ai-knowledge-hub.auth-expired", expired);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Authentication required" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(getKnowledgeBases()).rejects.toMatchObject({ status: 401 });
    expect(getAuthToken()).toBeNull();
    expect(expired).toHaveBeenCalledTimes(1);
    window.removeEventListener("ai-knowledge-hub.auth-expired", expired);
  });
});
