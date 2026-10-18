export type Role = "admin" | "viewer";

export interface User {
  id: number;
  email: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface SessionInfo {
  user: User;
  csrf_token: string;
}
