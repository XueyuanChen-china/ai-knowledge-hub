"use client";

import { Button, Group, Modal, Stack, Text } from "@mantine/core";

import type { KnowledgeBase } from "@/lib/api/types";

export function KnowledgeBaseDeleteModal({
  knowledgeBase,
  opened,
  loading = false,
  onClose,
  onConfirm,
}: {
  knowledgeBase: KnowledgeBase | null;
  opened: boolean;
  loading?: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  return (
    // 删除确认单独抽成组件，列表页和详情页都能复用同一套交互。
    <Modal
      opened={opened}
      onClose={onClose}
      title="删除知识库"
      centered
      size="md"
    >
      <Stack gap="md">
        <Text size="sm">
          这会删除知识库记录。当前第一版后端还没有做级联删除保护，删除前先确认没有依赖数据。
        </Text>
        <Text fw={600}>{knowledgeBase?.name ?? "-"}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            取消
          </Button>
          <Button color="red" loading={loading} onClick={onConfirm}>
            确认删除
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
