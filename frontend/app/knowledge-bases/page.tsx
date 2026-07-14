"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Loader, Modal, Stack } from "@mantine/core";
import { IconAlertCircle, IconPlus } from "@tabler/icons-react";

import { KnowledgeBaseDeleteModal } from "@/components/knowledge-base-delete-modal";
import { KnowledgeBaseForm } from "@/components/knowledge-base-form";
import { KnowledgeBaseTable } from "@/components/knowledge-base-table";
import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  createKnowledgeBase,
  deleteKnowledgeBase,
  getKnowledgeBases,
} from "@/lib/api/client";
import type { KnowledgeBase, KnowledgeBasePayload } from "@/lib/api/types";

export default function KnowledgeBasesPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const [createOpened, setCreateOpened] = useState(false);
  const [deleteOpened, setDeleteOpened] = useState(false);
  const [selectedKnowledgeBase, setSelectedKnowledgeBase] =
    useState<KnowledgeBase | null>(null);

  async function loadKnowledgeBasesList() {
    try {
      setLoading(true);
      setError("");
      setKnowledgeBases(await getKnowledgeBases());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "知识库列表加载失败",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // 页面首次进入时先把知识库列表拉下来。
    void loadKnowledgeBasesList();
  }, []);

  async function handleCreate(values: KnowledgeBasePayload) {
    try {
      setSubmitting(true);
      setError("");
      const created = await createKnowledgeBase(values);
      // 新建成功后直接把新记录插到当前列表最前面，避免再发一次全量查询。
      setKnowledgeBases((current) => [created, ...current]);
      setCreateOpened(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识库创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete() {
    if (!selectedKnowledgeBase) {
      return;
    }

    try {
      setDeleting(true);
      setError("");
      await deleteKnowledgeBase(selectedKnowledgeBase.id);
      setKnowledgeBases((current) =>
        current.filter((item) => item.id !== selectedKnowledgeBase.id),
      );
      setDeleteOpened(false);
      setSelectedKnowledgeBase(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识库删除失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <Stack gap="lg">
      <PageHeader
        title="知识库"
        description="这一页负责知识库主入口：列表展示、创建、删除，以及进入详情页做编辑。"
        rightSection={
          <Button
            leftSection={<IconPlus size={16} />}
            onClick={() => setCreateOpened(true)}
          >
            新建知识库
          </Button>
        }
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="加载失败">
          {error}
        </Alert>
      ) : null}

      {loading ? (
        <Loader size="sm" />
      ) : (
        <KnowledgeBaseTable
          knowledgeBases={knowledgeBases}
          showActions
          onDelete={(knowledgeBase) => {
            // 删除弹窗本身不查数据，这里先把当前行记录塞进去给弹窗展示。
            setSelectedKnowledgeBase(knowledgeBase);
            setDeleteOpened(true);
          }}
        />
      )}

      <Modal
        opened={createOpened}
        onClose={() => setCreateOpened(false)}
        title="新建知识库"
        centered
        size="lg"
      >
        {/* 创建知识库和详情编辑复用同一份表单组件，减少重复代码。 */}
        <KnowledgeBaseForm
          loading={submitting}
          submitLabel="创建知识库"
          onSubmit={handleCreate}
          onCancel={() => setCreateOpened(false)}
        />
      </Modal>

      <KnowledgeBaseDeleteModal
        knowledgeBase={selectedKnowledgeBase}
        opened={deleteOpened}
        loading={deleting}
        onClose={() => {
          setDeleteOpened(false);
          setSelectedKnowledgeBase(null);
        }}
        onConfirm={handleDelete}
      />
    </Stack>
  );
}
