import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { apiFetch } from "../api/client";

// Module-level variable, not React state — survives re-renders without
// ever touching localStorage/sessionStorage, per §4.2 / §18.
let accessToken: string | null = null;

export function getAccessToken() {
  return accessToken;
}
export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function clearAuth() {
  accessToken = null;
}

type User = {
  id: string;
  email: string;
  orgName: string;
  permissions: string[];
};

type AuthContextValue = {
  user: User | null;
  isLoading: boolean;
  can: (permission: string) => boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, orgName: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadMe = useCallback(async () => {
    try {
      const me = await apiFetch<User>("/auth/me");
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // On mount, try a silent refresh — covers the case where the person
    // already has a valid HttpOnly refresh cookie from a prior session.
    loadMe();
  }, [loadMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await apiFetch<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe]
  );

  const register = useCallback(
    async (email: string, password: string, orgName: string) => {
      const res = await apiFetch<{ access_token: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, org_name: orgName }),
      });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe]
  );

  const logout = useCallback(async () => {
    await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
    clearAuth();
    setUser(null);
  }, []);

  const can = useCallback(
    (permission: string) => user?.permissions.includes(permission) ?? false,
    [user]
  );

  return (
    <AuthContext.Provider value={{ user, isLoading, can, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}