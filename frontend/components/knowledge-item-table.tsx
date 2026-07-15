"use client";

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
import { IconChevronRight, IconPencil, IconTrash } from "@tabler/icons-react";

import type { KnowledgeItem } from "@/lib/api/types";

function getStatusColor(status: KnowledgeItem["status"]) {
  if (status === "active") {
    return "teal";
  }
  if (status === "disabled") {
    return "gray";
  }
  return "yellow";
}

export function KnowledgeItemTable({
  knowledgeItems,
  onEdit,
  onDelete,
  detailHrefBuilder,
}: {
  knowledgeItems: KnowledgeItem[];
  onEdit?: (knowledgeItem: KnowledgeItem) => void;
  onDelete?: (knowledgeItem: KnowledgeItem) => void;
  detailHrefBuilder?: (knowledgeItem: KnowledgeItem) => string;
}) {
  const rows = knowledgeItems.map((knowledgeItem) => {
    const detailHref = detailHrefBuilder?.(knowledgeItem);

    return (
      <Table.Tr key={knowledgeItem.id}>
        <Table.Td>{knowledgeItem.id}</Table.Td>
        <Table.Td>
          {detailHref ? (
            <Text component={Link} href={detailHref} fw={600} size="sm">
              {knowledgeItem.title}
            </Text>
          ) : (
            <Text fw={600} size="sm">
              {knowledgeItem.title}
            </Text>
          )}
        </Table.Td>
        <Table.Td>
          <Badge variant="light" color={getStatusColor(knowledgeItem.status)}>
            {knowledgeItem.status}
          </Badge>
        </Table.Td>
        <Table.Td>
          <Badge
            variant="light"
            color={knowledgeItem.source_type === "document" ? "blue" : "violet"}
          >
            {knowledgeItem.source_type}
          </Badge>
        </Table.Td>
        <Table.Td>
          <Text size="sm" c="dimmed" lineClamp={2}>
            {knowledgeItem.content || "暂无正文"}
          </Text>
        </Table.Td>
        <Table.Td>
          <Text size="sm" c="dimmed">
            {knowledgeItem.tags || "-"}
          </Text>
        </Table.Td>
        <Table.Td>
          <Group justify="flex-end" gap={6} wrap="nowrap">
            {detailHref ? (
              <Tooltip label="进入详情">
                <ActionIcon
                  component={Link}
                  href={detailHref}
                  variant="light"
                  color="blue"
                  aria-label="进入详情"
                >
                  <IconChevronRight size={16} />
                </ActionIcon>
              </Tooltip>
            ) : null}
            <Tooltip label="编辑条目">
              <ActionIcon
                variant="light"
                color="indigo"
                aria-label="编辑条目"
                onClick={() => onEdit?.(knowledgeItem)}
              >
                <IconPencil size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="删除条目">
              <ActionIcon
                variant="light"
                color="red"
                aria-label="删除条目"
                onClick={() => onDelete?.(knowledgeItem)}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Table.Td>
      </Table.Tr>
    );
  });

  return (
    <Card withBorder radius="sm" padding="lg">
      <Group justify="space-between" mb="md">
        <Text fw={700}>知识条目</Text>
        <Badge variant="light">{knowledgeItems.length} 条</Badge>
      </Group>
      <Table highlightOnHover verticalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>ID</Table.Th>
            <Table.Th>标题</Table.Th>
            <Table.Th>状态</Table.Th>
            <Table.Th>来源</Table.Th>
            <Table.Th>内容预览</Table.Th>
            <Table.Th>标签</Table.Th>
            <Table.Th ta="right">操作</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>{rows}</Table.Tbody>
      </Table>
    </Card>
  );
}
