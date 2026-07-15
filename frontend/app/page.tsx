"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Card,
  Grid,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconDatabase,
  IconFileText,
  IconMessageCircle,
} from "@tabler/icons-react";

import { KnowledgeBaseTable } from "@/components/knowledge-base-table";
import { PageHeader } from "@/components/page-header";
import { ApiError, getDashboardSummary } from "@/lib/api/client";
import type { DashboardSummary } from "@/lib/api/types";

const EMPTY_SUMMARY: DashboardSummary = {
  knowledgeBases: [],
  knowledgeItems: [],
};

export default function HomePage() {
  const [summary, setSummary] = useState<DashboardSummary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        setError("");
        const data = await getDashboardSummary();
        setSummary(data);
      } catch (err) {
        const message =
          err instanceof ApiError
            ? `后端请求失败：${err.message}`
            : "无法连接后端服务";
        setError(message);
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  const activeCount = useMemo(
    () => summary.knowledgeItems.filter((item) => item.status === "active").length,
    [summary.knowledgeItems],
  );

  const draftCount = useMemo(
    () => summary.knowledgeItems.filter((item) => item.status === "draft").length,
    [summary.knowledgeItems],
  );

  return (
    <Stack gap="lg">
      <PageHeader
        title="知识库总览"
        description="前端主工作台已经接上知识库、文档上传索引和对话入口，后续继续往搜索、审核和运营能力扩展。"
      />

      {error ? (
        <Alert
          color="red"
          icon={<IconAlertCircle size={18} />}
          title="后端未连接"
        >
          {error}
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
        <MetricCard
          label="知识库"
          value={summary.knowledgeBases.length}
          icon={<IconDatabase size={18} />}
        />
        <MetricCard
          label="知识条目"
          value={summary.knowledgeItems.length}
          icon={<IconFileText size={18} />}
        />
        <MetricCard
          label="Active 条目"
          value={activeCount}
          icon={<IconMessageCircle size={18} />}
        />
        <MetricCard
          label="Draft 条目"
          value={draftCount}
          icon={<IconFileText size={18} />}
        />
      </SimpleGrid>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, lg: 8 }}>
          {loading ? (
            <Card withBorder padding="lg">
              <Loader size="sm" />
            </Card>
          ) : (
            <KnowledgeBaseTable knowledgeBases={summary.knowledgeBases} />
          )}
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <Card withBorder radius="sm" padding="lg" h="100%">
            <Stack gap="md">
              <Text fw={700}>当前前端初始化范围</Text>
              <Text size="sm" c="dimmed">
                这版先完成路由、布局、API client 和基础页面。后续再把上传、索引、语义搜索、审核恢复逐步接上。
              </Text>
              <Text size="sm">已就绪模块：</Text>
              <Stack gap={8}>
                <Text size="sm">1. 首页总览</Text>
                <Text size="sm">2. 知识库列表页</Text>
                <Text size="sm">3. 文档上传与索引页</Text>
                <Text size="sm">4. 对话工作台入口页</Text>
              </Stack>
            </Stack>
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function MetricCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <Card withBorder padding="lg" radius="sm">
      <Stack gap={18}>
        <ThemeIcon variant="light" size={38} radius="sm">
          {icon}
        </ThemeIcon>
        <div>
          <Text c="dimmed" size="sm">
            {label}
          </Text>
          <Text fw={700} size="xl">
            {value}
          </Text>
        </div>
      </Stack>
    </Card>
  );
}
