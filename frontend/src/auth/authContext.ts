import { createContext } from 'react';
import type { AuthResponse, MeResponse, Permission } from '@/api/types';

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthValue {
  status: AuthStatus;
  me: MeResponse | null;
  /** Gate on permission strings, never on `role` (brief §4.4). */
  can: (permission: Permission) => boolean;
  /** Called by the login and register screens with the server's response. */
  signIn: (response: AuthResponse) => Promise<void>;
  signOut: () => Promise<void>;
  /** Set when the session was ended for a reason the user should be told about. */
  notice: string | null;
}

export const AuthContext = createContext<AuthValue | null>(null);
