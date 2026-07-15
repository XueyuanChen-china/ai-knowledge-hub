"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Modal,
  Loader,
  Stack,
  Text,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconArrowLeft,
  IconEdit,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";

import { KnowledgeBaseDeleteModal } from "@/components/knowledge-base-delete-modal";
import { KnowledgeBaseForm } from "@/components/knowledge-base-form";
import { KnowledgeItemDeleteModal } from "@/components/knowledge-item-delete-modal";
import { KnowledgeItemForm } from "@/components/knowledge-item-form";
import { KnowledgeItemTable } from "@/components/knowledge-item-table";
import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  createKnowledgeItem,
  deleteKnowledgeItem,
  deleteKnowledgeBase,
  getKnowledgeBase,
  getKnowledgeItems,
  updateKnowledgeItem,
  updateKnowledgeBase,
} from "@/lib/api/client";
import type {
  KnowledgeBase,
  KnowledgeBasePayload,
  KnowledgeItem,
  KnowledgeItemPayload,
} from "@/lib/api/types";

export default function KnowledgeBaseDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<number | null>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [knowledgeItems, setKnowledgeItems] = useState<KnowledgeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteOpened, setDeleteOpened] = useState(false);
  const [itemModalOpened, setItemModalOpened] = useState(false);
  const [itemSubmitting, setItemSubmitting] = useState(false);
  const [itemDeleting, setItemDeleting] = useState(false);
  const [itemDeleteOpened, setItemDeleteOpened] = useState(false);
  const [selectedKnowledgeItem, setSelectedKnowledgeItem] =
    useState<KnowledgeItem | null>(null);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  useEffect(() => {
    // 动态路由参数拿到的是字符串，这里统一转成 number，后面请求接口时直接复用。
    setKnowledgeBaseId(Number(params.id));
  }, [params]);

  useEffect(() => {
    if (knowledgeBaseId === null) {
      return;
    }
    const id = knowledgeBaseId;

    async function load() {
      try {
        setLoading(true);
        setError("");
        // 详情页需要同时展示主记录和条目数量，这里并发请求更合适。
        const [knowledgeBaseData, knowledgeItemsData] = await Promise.all([
          getKnowledgeBase(id),
          getKnowledgeItems(id),
        ]);
        setKnowledgeBase(knowledgeBaseData);
        setKnowledgeItems(knowledgeItemsData);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "知识库详情加载失败",
        );
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [knowledgeBaseId]);

  async function handleSave(values: KnowledgeBasePayload) {
    if (knowledgeBaseId === null) {
      return;
    }

    try {
      setSaving(true);
      setError("");
      setSuccessMessage("");
      const updated = await updateKnowledgeBase(knowledgeBaseId, values);
      // 保存成功后直接用后端返回值覆盖本地详情，避免页面上的时间和名称不同步。
      setKnowledgeBase(updated);
      setSuccessMessage("知识库已更新");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识库更新失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleItemSubmit(values: KnowledgeItemPayload) {
    try {
      setItemSubmitting(true);
      setError("");
      setSuccessMessage("");

      if (selectedKnowledgeItem) {
        const updated = await updateKnowledgeItem(selectedKnowledgeItem.id, values);
        setKnowledgeItems((current) =>
          current.map((item) => (item.id === updated.id ? updated : item)),
        );
        setSuccessMessage("知识条目已更新");
      } else {
        const created = await createKnowledgeItem(values);
        setKnowledgeItems((current) => [created, ...current]);
        setSuccessMessage("知识条目已创建");
      }

      setItemModalOpened(false);
      setSelectedKnowledgeItem(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目保存失败");
    } finally {
      setItemSubmitting(false);
    }
  }

  async function handleItemDelete() {
    if (!selectedKnowledgeItem) {
      return;
    }

    try {
      setItemDeleting(true);
      setError("");
      await deleteKnowledgeItem(selectedKnowledgeItem.id);
      setKnowledgeItems((current) =>
        current.filter((item) => item.id !== selectedKnowledgeItem.id),
      );
      setItemDeleteOpened(false);
      setSelectedKnowledgeItem(null);
      setSuccessMessage("知识条目已删除");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目删除失败");
    } finally {
      setItemDeleting(false);
    }
  }

  async function handleDelete() {
    if (knowledgeBaseId === null) {
      return;
    }

    try {
      setDeleting(true);
      setError("");
      await deleteKnowledgeBase(knowledgeBaseId);
      // 删除成功后回到列表页，并触发一次路由刷新，让列表拿到最新状态。
      router.push("/knowledge-bases");
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识库删除失败");
    } finally {
      setDeleting(false);
      setDeleteOpened(false);
    }
  }

  return (
    <Stack gap="lg">
      <PageHeader
        title={knowledgeBase ? knowledgeBase.name : "知识库详情"}
        description="在详情页里完成知识库编辑，同时把这个知识库下的知识条目管理和查看入口接出来。"
        rightSection={
          <Group>
            <Button
              onClick={() => {
                if (knowledgeBaseId === null) {
                  return;
                }
                setSelectedKnowledgeItem(null);
                setItemModalOpened(true);
              }}
              leftSection={<IconPlus size={16} />}
            >
              新建知识条目
            </Button>
            <Button
              component={Link}
              href="/knowledge-bases"
              variant="default"
              leftSection={<IconArrowLeft size={16} />}
            >
              返回列表
            </Button>
          </Group>
        }
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="操作失败">
          {error}
        </Alert>
      ) : null}

      {successMessage ? (
        <Alert color="green" title="保存成功">
          {successMessage}
        </Alert>
      ) : null}

      {loading ? (
        <Loader size="sm" />
      ) : knowledgeBase ? (
        <Grid gutter="md">
          <Grid.Col span={{ base: 12, lg: 8 }}>
            <Card withBorder radius="sm" padding="lg">
              <Group justify="space-between" mb="md">
                <Text fw={700}>编辑知识库</Text>
                <Badge variant="light">ID {knowledgeBase.id}</Badge>
              </Group>
              <KnowledgeBaseForm
                initialValues={{
                  name: knowledgeBase.name,
                  description: knowledgeBase.description,
                }}
                loading={saving}
                submitLabel="保存修改"
                onSubmit={handleSave}
              />
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 4 }}>
            <Stack gap="md">
              <Card withBorder radius="sm" padding="lg">
                <Stack gap="sm">
                  <Group gap={8}>
                    <IconEdit size={16} />
                    <Text fw={700}>概览</Text>
                  </Group>
                  <Text size="sm" c="dimmed">
                    创建时间：{new Date(knowledgeBase.created_at).toLocaleString("zh-CN")}
                  </Text>
                  <Text size="sm" c="dimmed">
                    更新时间：{new Date(knowledgeBase.updated_at).toLocaleString("zh-CN")}
                  </Text>
                  <Text size="sm" c="dimmed">
                    知识条目数：{knowledgeItems.length}
                  </Text>
                </Stack>
              </Card>
              <Card withBorder radius="sm" padding="lg">
                <Stack gap="sm">
                  <Group gap={8}>
                    <IconTrash size={16} />
                    <Text fw={700}>危险操作</Text>
                  </Group>
                  <Text size="sm" c="dimmed">
                    删除会移除知识库主记录。当前第一版后端还没有做复杂依赖保护，操作前先确认数据关系。
                  </Text>
                  <Button
                    color="red"
                    variant="light"
                    onClick={() => setDeleteOpened(true)}
                  >
                    删除知识库
                  </Button>
                </Stack>
              </Card>
            </Stack>
          </Grid.Col>
        </Grid>
      ) : null}

      {knowledgeBase ? (
        <KnowledgeItemTable
          knowledgeItems={knowledgeItems}
          detailHrefBuilder={(knowledgeItem) =>
            `/knowledge-items/${knowledgeItem.id}`
          }
          onEdit={(knowledgeItem) => {
            setSelectedKnowledgeItem(knowledgeItem);
            setItemModalOpened(true);
          }}
          onDelete={(knowledgeItem) => {
            setSelectedKnowledgeItem(knowledgeItem);
            setItemDeleteOpened(true);
          }}
        />
      ) : null}

      <KnowledgeBaseDeleteModal
        knowledgeBase={knowledgeBase}
        opened={deleteOpened}
        loading={deleting}
        onClose={() => setDeleteOpened(false)}
        onConfirm={handleDelete}
      />

      <Modal
        opened={itemModalOpened}
        onClose={() => {
          setItemModalOpened(false);
          setSelectedKnowledgeItem(null);
        }}
        title={selectedKnowledgeItem ? "编辑知识条目" : "新建知识条目"}
        centered
        size="lg"
      >
        {knowledgeBaseId !== null ? (
          <KnowledgeItemForm
            initialValues={{
              knowledge_base_id: knowledgeBaseId,
              title: selectedKnowledgeItem?.title ?? "",
              content: selectedKnowledgeItem?.content ?? "",
              tags: selectedKnowledgeItem?.tags ?? "",
              status: selectedKnowledgeItem?.status ?? "draft",
            }}
            loading={itemSubmitting}
            submitLabel={selectedKnowledgeItem ? "保存条目" : "创建条目"}
            onSubmit={handleItemSubmit}
            onCancel={() => {
              setItemModalOpened(false);
              setSelectedKnowledgeItem(null);
            }}
          />
        ) : null}
      </Modal>

      <KnowledgeItemDeleteModal
        knowledgeItem={selectedKnowledgeItem}
        opened={itemDeleteOpened}
        loading={itemDeleting}
        onClose={() => {
          setItemDeleteOpened(false);
          setSelectedKnowledgeItem(null);
        }}
        onConfirm={handleItemDelete}
      />
    </Stack>
  );
}
