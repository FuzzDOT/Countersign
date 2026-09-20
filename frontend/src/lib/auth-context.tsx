import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { request } from "../api/client";
import { getAccessToken, setAccessToken, onSessionEnded } from "../api/token";
import { isApiError, fieldErrors } from "../api/errors";

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
      const me = await request<User>("/auth/me");
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMe();
  }, [loadMe]);

  useEffect(() => {
    return onSessionEnded(() => {
      setUser(null);
    });
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await request<{ access_token: string }>("/auth/login", {
        method: "POST",
        json: { email, password },
        auth: false,
      });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe]
  );

  const register = useCallback(
    async (email: string, password: string, orgName: string) => {
      const res = await request<{ access_token: string }>("/auth/register", {
        method: "POST",
        json: { email, password, org_name: orgName },
        auth: false,
      });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe]
  );

  const logout = useCallback(async () => {
    await request("/auth/logout", { method: "POST" }).catch(() => {});
    setAccessToken(null);
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

export { isApiError, fieldErrors };
