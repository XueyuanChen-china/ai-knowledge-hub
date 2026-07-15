"use client";

import { Button, Group, Modal, Stack, Text } from "@mantine/core";

import type { KnowledgeItem } from "@/lib/api/types";

export function KnowledgeItemDeleteModal({
  knowledgeItem,
  opened,
  loading = false,
  onClose,
  onConfirm,
}: {
  knowledgeItem: KnowledgeItem | null;
  opened: boolean;
  loading?: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
}) {
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="删除知识条目"
      centered
      size="md"
    >
      <Stack gap="md">
        <Text size="sm">
          删除后这条知识记录会消失。当前版本还没有做删除前的 chunk 连带提示，操作前先确认来源和影响范围。
        </Text>
        <Text fw={600}>{knowledgeItem?.title ?? "-"}</Text>
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
