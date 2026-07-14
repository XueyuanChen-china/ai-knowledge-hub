import Link from "next/link";
import {
  ActionIcon,
  Badge,
  Card,
  Group,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import { IconChevronRight, IconTrash } from "@tabler/icons-react";

import type { KnowledgeBase } from "@/lib/api/types";

export function KnowledgeBaseTable({
  knowledgeBases,
  onDelete,
  showActions = false,
}: {
  knowledgeBases: KnowledgeBase[];
  onDelete?: (knowledgeBase: KnowledgeBase) => void;
  showActions?: boolean;
}) {
  // showActions=false 时，这个表格可以作为纯展示组件复用在首页总览里。
  const rows = knowledgeBases.map((knowledgeBase) => (
    <Table.Tr key={knowledgeBase.id}>
      <Table.Td>{knowledgeBase.id}</Table.Td>
      <Table.Td>
        {showActions ? (
          <Text
            component={Link}
            href={`/knowledge-bases/${knowledgeBase.id}`}
            fw={600}
            size="sm"
          >
            {knowledgeBase.name}
          </Text>
        ) : (
          <Text fw={600} size="sm">
            {knowledgeBase.name}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Text size="sm" c="dimmed" lineClamp={2}>
          {knowledgeBase.description || "暂无描述"}
        </Text>
      </Table.Td>
      <Table.Td>
        <Badge variant="light" color="gray">
          {new Date(knowledgeBase.updated_at).toLocaleDateString("zh-CN")}
        </Badge>
      </Table.Td>
      {showActions ? (
        <Table.Td>
          <Group justify="flex-end" gap={6} wrap="nowrap">
            <Tooltip label="进入详情">
              <ActionIcon
                component={Link}
                href={`/knowledge-bases/${knowledgeBase.id}`}
                variant="light"
                color="blue"
                aria-label="进入详情"
              >
                <IconChevronRight size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="删除知识库">
              <ActionIcon
                variant="light"
                color="red"
                aria-label="删除知识库"
                onClick={() => onDelete?.(knowledgeBase)}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Table.Td>
      ) : null}
    </Table.Tr>
  ));

  return (
    <Card withBorder radius="sm" padding="lg">
      <Group justify="space-between" mb="md">
        <Text fw={700}>知识库列表</Text>
        <Badge variant="light">{knowledgeBases.length} 个</Badge>
      </Group>
      <Table highlightOnHover verticalSpacing="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>ID</Table.Th>
          <Table.Th>名称</Table.Th>
          <Table.Th>描述</Table.Th>
          <Table.Th>更新时间</Table.Th>
          {showActions ? <Table.Th ta="right">操作</Table.Th> : null}
        </Table.Tr>
      </Table.Thead>
        <Table.Tbody>{rows}</Table.Tbody>
      </Table>
    </Card>
  );
}
