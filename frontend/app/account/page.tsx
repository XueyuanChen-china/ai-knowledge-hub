import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Divider,
  Group,
  Modal,
  PasswordInput,
  Stack,
  Text,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconKey,
  IconLogout,
  IconShieldLock,
} from "@tabler/icons-react";

import { useAuth } from "@/components/auth-context-value";
import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  changeCurrentPassword,
  logoutAllDevices,
} from "@/lib/api/client";

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "尚未登录";
}

export default function AccountPage() {
  const { user, organization, role } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [logoutAllOpened, setLogoutAllOpened] = useState(false);
  const [loggingOutAll, setLoggingOutAll] = useState(false);
  const [error, setError] = useState("");

  async function handleChangePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }

    try {
      setChangingPassword(true);
      setError("");
      await changeCurrentPassword(currentPassword, newPassword);
      // 修改密码会递增 token_version，当前 token 也必须重新登录。
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "密码修改失败");
    } finally {
      setChangingPassword(false);
    }
  }

  async function handleLogoutAll() {
    try {
      setLoggingOutAll(true);
      setError("");
      await logoutAllDevices();
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "退出全部设备失败");
    } finally {
      setLoggingOutAll(false);
      setLogoutAllOpened(false);
    }
  }

  return (
    <Stack gap="lg" maw={880}>
      <PageHeader
        title="我的账号"
        description="查看当前组织身份，并管理密码和登录设备。"
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="操作失败">
          {error}
        </Alert>
      ) : null}
      <Card withBorder radius="sm" padding="lg">
        <Stack gap="md">
          <Group justify="space-between" align="flex-start">
            <div>
              <Text fw={600}>账号信息</Text>
              <Text size="sm" c="dimmed" mt={4}>
                当前会话身份由后端 JWT、组织成员关系和 token version 共同验证。
              </Text>
            </div>
            <IconShieldLock size={22} />
          </Group>
          <Divider />
          <Group grow align="flex-start">
            <Stack gap={2}>
              <Text size="xs" c="dimmed">邮箱</Text>
              <Text size="sm">{user.email}</Text>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed">组织与角色</Text>
              <Text size="sm">{organization.name} · {role}</Text>
            </Stack>
            <Stack gap={2}>
              <Text size="xs" c="dimmed">上次登录</Text>
              <Text size="sm">{formatDate(user.last_login_at)}</Text>
            </Stack>
          </Group>
        </Stack>
      </Card>

      <Card withBorder radius="sm" padding="lg">
        <form onSubmit={handleChangePassword}>
          <Stack gap="md">
            <div>
              <Text fw={600}>修改密码</Text>
              <Text size="sm" c="dimmed" mt={4}>
                成功后会立即退出当前设备，其他旧 token 也将失效。
              </Text>
            </div>
            <PasswordInput
              label="当前密码"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <PasswordInput
              label="新密码"
              description="至少 8 位"
              value={newPassword}
              onChange={(event) => setNewPassword(event.currentTarget.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
            <PasswordInput
              label="确认新密码"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.currentTarget.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
            <Group justify="flex-end">
              <Button
                type="submit"
                loading={changingPassword}
                leftSection={<IconKey size={16} />}
              >
                更新密码并重新登录
              </Button>
            </Group>
          </Stack>
        </form>
      </Card>

      <Card withBorder radius="sm" padding="lg">
        <Stack gap="md">
          <div>
            <Text fw={600}>设备与会话</Text>
            <Text size="sm" c="dimmed" mt={4}>
              退出全部设备会递增 token version，使所有已签发 access token 立即失效。
            </Text>
          </div>
          <Group justify="flex-end">
            <Button
              color="red"
              variant="light"
              leftSection={<IconLogout size={16} />}
              onClick={() => setLogoutAllOpened(true)}
            >
              退出全部设备
            </Button>
          </Group>
        </Stack>
      </Card>

      <Modal
        opened={logoutAllOpened}
        onClose={() => setLogoutAllOpened(false)}
        title="退出全部设备"
        centered
      >
        <Stack gap="lg">
          <Text size="sm">
            当前账号在所有浏览器和设备上的 access token 都会立即失效，需要重新登录。
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setLogoutAllOpened(false)}>
              取消
            </Button>
            <Button color="red" loading={loggingOutAll} onClick={() => void handleLogoutAll()}>
              确认退出
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
