"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getCurrentUser, updateUser } from "../../lib/usersApi"; // Adjust path as needed

type User = {
  id: number;
  nickname: string;
  email: string;
  role: string;
  image_url?: string | null;
  timezone?: string | null;
};

type AuthContextType = {
  token: string | null;
  setToken: (token: string | null) => void;
  isLoggedIn: boolean;
  user: User | null;
  logout: () => void;
  setRedirectAfterLogin: (path: string | null) => void;
  getRedirectAfterLogin: () => string | null;
  loading: boolean;
};

const AuthContext = createContext<AuthContextType>({
  token: null,
  setToken: () => {},
  isLoggedIn: false,
  user: null,
  logout: () => {},
  setRedirectAfterLogin: () => {},
  getRedirectAfterLogin: () => null,
  loading: true,
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setTokenState] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);

  // On mount, pull token from localStorage if it exists,
  // but do NOT set loading to false here
  useEffect(() => {
    const t = localStorage.getItem("token");
    if (t) {
      setTokenState(t);
      // let the next effect do the loading state
    } else {
      setLoading(false);
    }
    setInitialized(true);
  }, []);

  // Whenever token changes, (re-)fetch user
  useEffect(() => {
    if (!initialized) return;
    if (token) {
      setLoading(true);
      getCurrentUser(token)
        .then(async (data) => {
          setUser(data);
          setLoading(false);
          if (!data.timezone) {
            const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
            try {
              const updated = await updateUser(
                data.id,
                { timezone: tz },
                token,
              );
              setUser(updated);
              alert(`Timezone set to ${tz}`);
            } catch (e) {
              console.error("Failed to update timezone", e);
            }
          }
        })
        .catch(() => {
          setUser(null);
          setLoading(false);
          setTokenState(null);
          localStorage.removeItem("token");
        });
    } else {
      setUser(null);
      setLoading(false);
    }
  }, [token, initialized]);

  const setToken = (t: string | null) => {
    setTokenState(t);
    if (t) localStorage.setItem("token", t);
    else localStorage.removeItem("token");
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem("token");
  };

  const setRedirectAfterLogin = (path: string | null) => {
    if (path) sessionStorage.setItem("redirectAfterLogin", path);
    else sessionStorage.removeItem("redirectAfterLogin");
  };

  const getRedirectAfterLogin = () => {
    const path = sessionStorage.getItem("redirectAfterLogin");
    return path;
  };

  async function refreshUser(tokenOverride) {
    const tokenToUse = tokenOverride || token;
    if (!tokenToUse) return;
    try {
      const userData = await getCurrentUser(tokenToUse);
      setUser(userData);
    } catch (e) {
      // Optionally handle error, maybe setUser(null) or log out
      console.error("Failed to refresh user:", e);
    }
  }

  return (
    <AuthContext.Provider
      value={{
        token,
        setToken,
        isLoggedIn: !!token && !!user,
        user,
        logout,
        setRedirectAfterLogin,
        getRedirectAfterLogin,
        loading,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
