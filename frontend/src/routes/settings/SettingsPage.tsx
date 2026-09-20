import { useEffect, useState } from "react";
import { useAuth } from "../../lib/auth-context";
import { request } from "../../api/client";
import { isApiError } from "../../api/errors";
import { Button } from "../../components/primitives/Button";
import { Input } from "../../components/primitives/Input";
import { Badge } from "../../components/primitives/Badge";
import { Skeleton } from "../../components/primitives/Skeleton";
import { ErrorState } from "../../components/primitives/ErrorState";
import { EmptyState } from "../../components/primitives/EmptyState";

type Member = {
  id: string;
  email: string;
  role: "owner" | "analyst" | "viewer";
};

export default function SettingsPage() {
  const { user, can } = useAuth();

  const [members, setMembers] = useState<Member[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [membersLoading, setMembersLoading] = useState(true);

  const canManageUsers = can("users:manage");

  useEffect(() => {
    if (!canManageUsers) {
      setMembersLoading(false);
      return;
    }
    let alive = true;
    setMembersLoading(true);
    request<Member[]>("/org/members")
      .then((data) => {
        if (alive) setMembers(data);
      })
      .catch((err) => {
        if (!alive) return;
        setMembersError(isApiError(err) ? err.message : "Could not load members.");
      })
      .finally(() => {
        if (alive) setMembersLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [canManageUsers]);

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 flex flex-col gap-10">
      <div>
        <h1 className="text-display-2 text-ink-50">Settings</h1>
        <p className="text-body-sm text-ink-200 mt-1">Profile, organization, and members.</p>
      </div>

      {/* Profile */}
      <section className="flex flex-col gap-4">
        <h2 className="text-h2 text-ink-50">Profile</h2>
        <div className="panel p-6 flex flex-col gap-4">
          <Input label="Email" value={user?.email ?? ""} disabled readOnly />
          <p className="text-body-sm text-ink-200">
            Password changes and email updates are not available in this build.
          </p>
        </div>
      </section>

      {/* Organization */}
      <section className="flex flex-col gap-4">
        <h2 className="text-h2 text-ink-50">Organization</h2>
        <div className="panel p-6 flex flex-col gap-4">
          <Input label="Organization name" value={user?.orgName ?? ""} disabled readOnly />
        </div>
      </section>

      {/* Members — owner only */}
      {canManageUsers && (
        <section className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h2 className="text-h2 text-ink-50">Members</h2>
            <Button variant="secondary" disabled title="Not yet available">
              Invite member
            </Button>
          </div>

          <div className="panel p-0 overflow-hidden">
            {membersLoading && (
              <div className="p-6 flex flex-col gap-3">
                <Skeleton height="2.5rem" />
                <Skeleton height="2.5rem" />
                <Skeleton height="2.5rem" />
              </div>
            )}

            {!membersLoading && membersError && (
              <div className="p-6">
                <ErrorState message={membersError} />
              </div>
            )}

            {!membersLoading && !membersError && members && members.length === 0 && (
              <div className="p-6">
                <EmptyState title="No other members yet" />
              </div>
            )}

            {!membersLoading && !membersError && members && members.length > 0 && (
              <table className="w-full text-body-sm">
                <thead>
                  <tr className="border-b border-ink-500/40 text-left text-ink-200">
                    <th className="px-6 py-3 font-normal">Email</th>
                    <th className="px-6 py-3 font-normal">Role</th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((member) => (
                    <tr key={member.id} className="border-b border-ink-500/40 last:border-0">
                      <td className="px-6 py-3 text-ink-50">{member.email}</td>
                      <td className="px-6 py-3">
                        <Badge neutral>{member.role}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
