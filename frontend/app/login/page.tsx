import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Center,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconAlertCircle, IconLogin } from "@tabler/icons-react";

import { ApiError, login, setAuthToken } from "@/lib/api/client";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setLoading(true);
      setError("");
      const response = await login(email, password);
      setAuthToken(response.access_token);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Center mih="100vh" px="md" bg="gray.0">
      <Card withBorder shadow="sm" radius="md" padding="xl" w="100%" maw={420}>
        <form onSubmit={handleSubmit}>
          <Stack gap="lg">
            <div>
              <Title order={2}>登录 AI Knowledge Hub</Title>
              <Text size="sm" c="dimmed" mt={6}>
                使用组织账号进入知识库和专家问答工作台。
              </Text>
            </div>

            {error ? (
              <Alert color="red" icon={<IconAlertCircle size={18} />}>
                {error}
              </Alert>
            ) : null}

            <TextInput
              label="邮箱"
              placeholder="admin@example.com"
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              autoComplete="email"
              required
            />
            <PasswordInput
              label="密码"
              placeholder="请输入密码"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <Button
              type="submit"
              loading={loading}
              leftSection={<IconLogin size={16} />}
            >
              登录
            </Button>
          </Stack>
        </form>
      </Card>
    </Center>
  );
}
