"use client";

import { useEffect, useState } from "react";
import { Button, Group, Select, Stack, Textarea, TextInput } from "@mantine/core";

import type { KnowledgeItemPayload } from "@/lib/api/types";

interface KnowledgeItemFormProps {
  initialValues?: KnowledgeItemPayload;
  loading?: boolean;
  submitLabel: string;
  onSubmit: (values: KnowledgeItemPayload) => Promise<void> | void;
  onCancel?: () => void;
}

const STATUS_OPTIONS = [
  { value: "draft", label: "draft" },
  { value: "active", label: "active" },
  { value: "disabled", label: "disabled" },
] as const;

export function KnowledgeItemForm({
  initialValues,
  loading = false,
  submitLabel,
  onSubmit,
  onCancel,
}: KnowledgeItemFormProps) {
  const [values, setValues] = useState<KnowledgeItemPayload>(
    initialValues ?? {
      knowledge_base_id: 0,
      title: "",
      content: "",
      tags: "",
      status: "draft",
    },
  );

  useEffect(() => {
    if (!initialValues) {
      return;
    }
    // 编辑场景切换条目时，需要把表单内容同步成新的初始值。
    setValues(initialValues);
  }, [initialValues]);

  return (
    <form
      onSubmit={async (event) => {
        event.preventDefault();
        await onSubmit({
          ...values,
          title: values.title.trim(),
          content: values.content.trim(),
          tags: values.tags.trim(),
        });
      }}
    >
      <Stack gap="md">
        <TextInput
          label="知识条目标题"
          placeholder="例如：差旅报销流程"
          value={values.title}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setValues((current) => ({ ...current, title: value }));
          }}
          required
        />
        <Textarea
          label="知识内容"
          placeholder="把知识正文写在这里"
          minRows={8}
          value={values.content}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setValues((current) => ({ ...current, content: value }));
          }}
          required
        />
        <TextInput
          label="标签"
          placeholder='例如：报销, 制度, 财务 或 ["报销","制度"]'
          value={values.tags}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setValues((current) => ({ ...current, tags: value }));
          }}
        />
        <Select
          label="状态"
          data={STATUS_OPTIONS.map((item) => ({ ...item }))}
          value={values.status}
          onChange={(value) =>
            setValues((current) => ({
              ...current,
              status: (value as KnowledgeItemPayload["status"]) ?? "draft",
            }))
          }
          allowDeselect={false}
        />
        <Group justify="flex-end">
          {onCancel ? (
            <Button variant="default" onClick={onCancel}>
              取消
            </Button>
          ) : null}
          <Button
            type="submit"
            loading={loading}
            disabled={!values.title.trim() || !values.content.trim()}
          >
            {submitLabel}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
