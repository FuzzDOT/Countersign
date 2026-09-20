import { useEffect, useState } from 'react';
import { useAuth } from '../../auth/useAuth';
import { api } from '../../api/endpoints';
import type { MemberOut } from '../../api/types';
import { Button } from '../../components/primitives/Button';
import { Input } from '../../components/primitives/Input';
import { Badge } from '../../components/primitives/Badge';
import { Skeleton } from '../../components/primitives/Skeleton';
import { ErrorState } from '../../components/primitives/ErrorState';
import { EmptyState } from '../../components/primitives/EmptyState';

export default function SettingsPage() {
  const { me, can } = useAuth();
  const [members, setMembers] = useState<MemberOut[] | null>(null);
  const [membersError, setMembersError] = useState<unknown>(null);
  const [membersLoading, setMembersLoading] = useState(true);

  const canManageUsers = can('users:manage');

  useEffect(() => {
    if (!canManageUsers) {
      setMembersLoading(false);
      return;
    }
    let alive = true;
    setMembersLoading(true);
    api.org
      .members()
      .then((data) => {
        if (alive) setMembers(data);
      })
      .catch((err) => {
        if (alive) setMembersError(err);
      })
      .finally(() => {
        if (alive) setMembersLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [canManageUsers]);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-10 px-6 py-10">
      <div>
        <h1 className="text-display-2 text-ink-50">Settings</h1>
        <p className="mt-1 text-body-sm text-ink-200">Profile, organization, and members.</p>
      </div>

      {/* Profile */}
      <section className="flex flex-col gap-4">
        <h2 className="text-h2 text-ink-50">Profile</h2>
        <div className="panel flex flex-col gap-4 p-6">
          <Input label="Email" value={me?.email ?? ''} disabled readOnly />
          <p className="text-body-sm text-ink-200">
            Password changes and email updates are not available in this build.
          </p>
        </div>
      </section>

      {/* Organization */}
      <section className="flex flex-col gap-4">
        <h2 className="text-h2 text-ink-50">Organization</h2>
        <div className="panel flex flex-col gap-4 p-6">
          <Input label="Organization name" value={me?.org_name ?? ''} disabled readOnly />
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

          <div className="panel overflow-hidden p-0">
            {membersLoading && (
              <div className="flex flex-col gap-3 p-6">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            )}

            {!membersLoading && Boolean(membersError) && (
              <div className="p-6">
                <ErrorState error={membersError} compact />
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
                        <Badge>{member.role}</Badge>
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
