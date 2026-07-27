import { useEffect, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Menu,
  Modal,
  PasswordInput,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconDots,
  IconKey,
  IconPlus,
  IconShieldCheck,
  IconTrash,
  IconUserCheck,
  IconUserOff,
} from "@tabler/icons-react";

import { useAuth } from "@/components/auth-context-value";
import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  createOrganizationMember,
  getOrganizationMembers,
  getSecurityAuditLogs,
  removeOrganizationMember,
  resetOrganizationMemberPassword,
  updateOrganizationMemberRole,
  updateOrganizationMemberStatus,
} from "@/lib/api/client";
import type {
  OrganizationMember,
  OrganizationRole,
  SecurityAuditLog,
} from "@/lib/api/types";

const ROLE_OPTIONS = [
  { value: "owner", label: "Owner" },
  { value: "admin", label: "Admin" },
  { value: "editor", label: "Editor" },
  { value: "viewer", label: "Viewer" },
];

const ROLE_COLORS: Record<OrganizationRole, string> = {
  owner: "violet",
  admin: "blue",
  editor: "teal",
  viewer: "gray",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN");
}

export default function UserManagementPage() {
  const { role: currentRole, user: currentUser } = useAuth();
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [auditLogs, setAuditLogs] = useState<SecurityAuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [auditLoading, setAuditLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [createOpened, setCreateOpened] = useState(false);
  const [roleOpened, setRoleOpened] = useState(false);
  const [resetOpened, setResetOpened] = useState(false);
  const [removeOpened, setRemoveOpened] = useState(false);
  const [selectedMember, setSelectedMember] = useState<OrganizationMember | null>(null);
  const [email, setEmail] = useState("");
  const [initialPassword, setInitialPassword] = useState("");
  const [newMemberRole, setNewMemberRole] = useState<OrganizationRole>("viewer");
  const [selectedRole, setSelectedRole] = useState<OrganizationRole>("viewer");
  const [resetPassword, setResetPassword] = useState("");

  const canManageUsers = ["owner", "admin"].includes(currentRole);
  const canAssignOwner = currentRole === "owner";

  async function loadData() {
    try {
      setLoading(true);
      setAuditLoading(true);
      setError("");
      const [memberList, auditResponse] = await Promise.all([
        getOrganizationMembers(),
        getSecurityAuditLogs(),
      ]);
      setMembers(memberList);
      setAuditLogs(auditResponse.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "账号管理数据加载失败");
    } finally {
      setLoading(false);
      setAuditLoading(false);
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  function updateMember(updated: OrganizationMember) {
    setMembers((current) =>
      current.map((member) =>
        member.user.id === updated.user.id ? updated : member,
      ),
    );
  }

  function openRoleModal(member: OrganizationMember) {
    setSelectedMember(member);
    setSelectedRole(member.role);
    setRoleOpened(true);
  }

  async function handleCreateMember(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setSaving(true);
      setError("");
      const created = await createOrganizationMember({
        email,
        initial_password: initialPassword,
        role: newMemberRole,
      });
      setMembers((current) => [created, ...current]);
      setEmail("");
      setInitialPassword("");
      setNewMemberRole("viewer");
      setCreateOpened(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成员创建失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleStatusChange(member: OrganizationMember) {
    try {
      setSaving(true);
      setError("");
      updateMember(
        await updateOrganizationMemberStatus(member.user.id, !member.user.is_active),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成员状态更新失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleRoleChange() {
    if (!selectedMember) return;
    try {
      setSaving(true);
      setError("");
      updateMember(
        await updateOrganizationMemberRole(selectedMember.user.id, selectedRole),
      );
      setRoleOpened(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "角色更新失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleResetPassword() {
    if (!selectedMember) return;
    try {
      setSaving(true);
      setError("");
      await resetOrganizationMemberPassword(selectedMember.user.id, resetPassword);
      setResetPassword("");
      setResetOpened(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "密码重置失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemoveMember() {
    if (!selectedMember) return;
    try {
      setSaving(true);
      setError("");
      await removeOrganizationMember(selectedMember.user.id);
      setMembers((current) =>
        current.filter((member) => member.user.id !== selectedMember.user.id),
      );
      setRemoveOpened(false);
      setSelectedMember(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "成员移除失败");
    } finally {
      setSaving(false);
    }
  }

  if (!canManageUsers) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={18} />} title="无访问权限">
        当前角色没有管理组织成员的权限。
      </Alert>
    );
  }

  const roleOptions = canAssignOwner
    ? ROLE_OPTIONS
    : ROLE_OPTIONS.filter((option) => option.value !== "owner");

  return (
    <Stack gap="lg">
      <PageHeader
        title="成员管理"
        description="管理当前组织的账号、角色、访问状态，并查看关键身份操作记录。"
        rightSection={
          <Button leftSection={<IconPlus size={16} />} onClick={() => setCreateOpened(true)}>
            创建成员
          </Button>
        }
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="操作失败">
          {error}
        </Alert>
      ) : null}

      <Tabs defaultValue="members" variant="outline">
        <Tabs.List>
          <Tabs.Tab value="members" leftSection={<IconShieldCheck size={15} />}>
            组织成员
          </Tabs.Tab>
          <Tabs.Tab value="audit" leftSection={<IconKey size={15} />}>
            安全审计
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="members" pt="md">
          <Card withBorder radius="sm" padding="lg">
            {loading ? (
              <Loader size="sm" />
            ) : (
              <Table.ScrollContainer minWidth={760}>
                <Table verticalSpacing="sm" highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>成员</Table.Th>
                      <Table.Th>角色</Table.Th>
                      <Table.Th>状态</Table.Th>
                      <Table.Th>最近登录</Table.Th>
                      <Table.Th>加入时间</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {members.map((member) => {
                      const isCurrentUser = member.user.id === currentUser.id;
                      const targetIsOwner = member.role === "owner";
                      const adminCannotTouchOwner = currentRole !== "owner" && targetIsOwner;
                      return (
                        <Table.Tr key={member.membership_id}>
                          <Table.Td>
                            <Stack gap={1}>
                              <Text size="sm" fw={500}>{member.user.email}</Text>
                              {isCurrentUser ? <Text size="xs" c="dimmed">当前账号</Text> : null}
                            </Stack>
                          </Table.Td>
                          <Table.Td>
                            <Badge size="sm" color={ROLE_COLORS[member.role]} variant="light">
                              {member.role}
                            </Badge>
                          </Table.Td>
                          <Table.Td>
                            <Badge size="sm" color={member.user.is_active ? "green" : "gray"} variant="light">
                              {member.user.is_active ? "已启用" : "已禁用"}
                            </Badge>
                          </Table.Td>
                          <Table.Td><Text size="sm">{formatDate(member.user.last_login_at ?? member.user.created_at)}</Text></Table.Td>
                          <Table.Td><Text size="sm">{formatDate(member.joined_at)}</Text></Table.Td>
                          <Table.Td>
                            <Menu shadow="md" width={180} position="bottom-end">
                              <Menu.Target>
                                <ActionIcon variant="subtle" color="gray" aria-label="成员操作">
                                  <IconDots size={18} />
                                </ActionIcon>
                              </Menu.Target>
                              <Menu.Dropdown>
                                <Menu.Item
                                  disabled={adminCannotTouchOwner}
                                  leftSection={<IconShieldCheck size={15} />}
                                  onClick={() => openRoleModal(member)}
                                >
                                  修改角色
                                </Menu.Item>
                                <Menu.Item
                                  disabled={adminCannotTouchOwner || isCurrentUser}
                                  leftSection={member.user.is_active ? <IconUserOff size={15} /> : <IconUserCheck size={15} />}
                                  onClick={() => void handleStatusChange(member)}
                                >
                                  {member.user.is_active ? "禁用账号" : "启用账号"}
                                </Menu.Item>
                                <Menu.Item
                                  disabled={adminCannotTouchOwner}
                                  leftSection={<IconKey size={15} />}
                                  onClick={() => {
                                    setSelectedMember(member);
                                    setResetOpened(true);
                                  }}
                                >
                                  重置密码
                                </Menu.Item>
                                <Menu.Divider />
                                <Menu.Item
                                  color="red"
                                  disabled={adminCannotTouchOwner || isCurrentUser}
                                  leftSection={<IconTrash size={15} />}
                                  onClick={() => {
                                    setSelectedMember(member);
                                    setRemoveOpened(true);
                                  }}
                                >
                                  移除成员
                                </Menu.Item>
                              </Menu.Dropdown>
                            </Menu>
                          </Table.Td>
                        </Table.Tr>
                      );
                    })}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="audit" pt="md">
          <Card withBorder radius="sm" padding="lg">
            {auditLoading ? (
              <Loader size="sm" />
            ) : (
              <Table.ScrollContainer minWidth={760}>
                <Table verticalSpacing="sm" highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>时间</Table.Th>
                      <Table.Th>动作</Table.Th>
                      <Table.Th>操作人</Table.Th>
                      <Table.Th>对象</Table.Th>
                      <Table.Th>结果</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {auditLogs.map((log) => (
                      <Table.Tr key={log.id}>
                        <Table.Td><Text size="sm">{formatDate(log.created_at)}</Text></Table.Td>
                        <Table.Td><Text size="sm">{log.action}</Text></Table.Td>
                        <Table.Td><Text size="sm">{log.actor_email || "未认证请求"}</Text></Table.Td>
                        <Table.Td><Text size="sm">{log.target_type || "-"} {log.target_id || ""}</Text></Table.Td>
                        <Table.Td><Badge size="sm" color={log.outcome === "success" ? "green" : "orange"} variant="light">{log.outcome}</Badge></Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </Card>
        </Tabs.Panel>
      </Tabs>

      <Modal opened={createOpened} onClose={() => setCreateOpened(false)} title="创建组织成员" centered>
        <form onSubmit={handleCreateMember}>
          <Stack gap="md">
            <TextInput label="邮箱" value={email} onChange={(event) => setEmail(event.currentTarget.value)} required />
            <PasswordInput label="初始密码" description="至少 8 位，请通过安全渠道交付给成员。" value={initialPassword} onChange={(event) => setInitialPassword(event.currentTarget.value)} minLength={8} required />
            <Select label="组织角色" data={roleOptions} value={newMemberRole} onChange={(value) => setNewMemberRole((value ?? "viewer") as OrganizationRole)} allowDeselect={false} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setCreateOpened(false)}>取消</Button>
              <Button type="submit" loading={saving}>创建成员</Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      <Modal opened={roleOpened} onClose={() => setRoleOpened(false)} title="修改成员角色" centered>
        <Stack gap="md">
          <Text size="sm">{selectedMember?.user.email}</Text>
          <Select label="组织角色" data={roleOptions} value={selectedRole} onChange={(value) => setSelectedRole((value ?? "viewer") as OrganizationRole)} allowDeselect={false} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRoleOpened(false)}>取消</Button>
            <Button loading={saving} onClick={() => void handleRoleChange()}>保存角色</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={resetOpened} onClose={() => setResetOpened(false)} title="重置成员密码" centered>
        <Stack gap="md">
          <Text size="sm">重置后，{selectedMember?.user.email} 的所有旧 token 会失效。</Text>
          <PasswordInput label="新密码" value={resetPassword} onChange={(event) => setResetPassword(event.currentTarget.value)} minLength={8} required />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setResetOpened(false)}>取消</Button>
            <Button loading={saving} onClick={() => void handleResetPassword()}>确认重置</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={removeOpened} onClose={() => setRemoveOpened(false)} title="移除组织成员" centered>
        <Stack gap="lg">
          <Text size="sm">移除后，该成员将无法再访问当前组织；如果这是其唯一组织关系，账号也会被禁用。</Text>
          <Text size="sm" fw={600}>{selectedMember?.user.email}</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRemoveOpened(false)}>取消</Button>
            <Button color="red" loading={saving} onClick={() => void handleRemoveMember()}>移除成员</Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
