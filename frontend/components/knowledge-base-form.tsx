"use client";

import { useEffect, useState } from "react";
import { Button, Group, Stack, Textarea, TextInput } from "@mantine/core";

import type { KnowledgeBasePayload } from "@/lib/api/types";

interface KnowledgeBaseFormProps {
  initialValues?: KnowledgeBasePayload;
  loading?: boolean;
  submitLabel: string;
  onSubmit: (values: KnowledgeBasePayload) => Promise<void> | void;
  onCancel?: () => void;
}

const EMPTY_VALUES: KnowledgeBasePayload = {
  name: "",
  description: "",
};

export function KnowledgeBaseForm({
  initialValues = EMPTY_VALUES,
  loading = false,
  submitLabel,
  onSubmit,
  onCancel,
}: KnowledgeBaseFormProps) {
  const [values, setValues] = useState<KnowledgeBasePayload>(initialValues);

  useEffect(() => {
    // 当外部传入的初始值变化时（比如从创建切到编辑），同步重置表单内容。
    setValues(initialValues);
  }, [initialValues]);

  return (
    <form
      onSubmit={async (event) => {
        event.preventDefault();
        // 提交前先 trim，一方面减少脏数据，另一方面也避免只有空格还能提交。
        await onSubmit({
          name: values.name.trim(),
          description: values.description.trim(),
        });
      }}
    >
      <Stack gap="md">
        <TextInput
          label="知识库名称"
          placeholder="例如：公司制度知识库"
          value={values.name}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setValues((current) => ({
              ...current,
              name: value,
            }));
          }}
          required
        />
        <Textarea
          label="知识库描述"
          placeholder="说明这个知识库的范围、用途和目标用户"
          minRows={5}
          value={values.description}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setValues((current) => ({
              ...current,
              description: value,
            }));
          }}
        />
        <Group justify="flex-end">
          {onCancel ? (
            <Button variant="default" onClick={onCancel}>
              取消
            </Button>
          ) : null}
          <Button type="submit" loading={loading} disabled={!values.name.trim()}>
            {submitLabel}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
