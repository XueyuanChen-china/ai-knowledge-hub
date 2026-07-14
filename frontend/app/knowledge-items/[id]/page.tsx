"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Grid,
  Group,
  Loader,
  Modal,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconArrowLeft,
  IconEdit,
  IconTrash,
} from "@tabler/icons-react";

import { KnowledgeItemDeleteModal } from "@/components/knowledge-item-delete-modal";
import { KnowledgeItemForm } from "@/components/knowledge-item-form";
import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  deleteKnowledgeItem,
  getKnowledgeBase,
  getKnowledgeItem,
  getKnowledgeItemChunks,
  indexKnowledgeItem,
  splitKnowledgeItemIntoChunks,
  updateKnowledgeItem,
} from "@/lib/api/client";
import type {
  ChunkRecord,
  KnowledgeBase,
  KnowledgeItem,
  KnowledgeItemChunkResponse,
  KnowledgeItemIndexResponse,
  KnowledgeItemPayload,
} from "@/lib/api/types";

function safeParseMetadata(metadataJson: string) {
  try {
    return JSON.parse(metadataJson) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export default function KnowledgeItemDetailPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const [knowledgeItemId, setKnowledgeItemId] = useState<number | null>(null);
  const [knowledgeItem, setKnowledgeItem] = useState<KnowledgeItem | null>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [chunks, setChunks] = useState<ChunkRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [chunking, setChunking] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [editOpened, setEditOpened] = useState(false);
  const [deleteOpened, setDeleteOpened] = useState(false);
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [processingResult, setProcessingResult] = useState<
    KnowledgeItemChunkResponse | KnowledgeItemIndexResponse | null
  >(null);

  const parsedChunkMetadata = useMemo(
    () =>
      chunks.map((chunk) => ({
        chunkId: chunk.id,
        metadata: safeParseMetadata(chunk.metadata_json),
      })),
    [chunks],
  );

  useEffect(() => {
    setKnowledgeItemId(Number(params.id));
  }, [params]);

  useEffect(() => {
    if (knowledgeItemId === null) {
      return;
    }
    const id = knowledgeItemId;

    async function load() {
      try {
        setLoading(true);
        setError("");

        const knowledgeItemData = await getKnowledgeItem(id);
        const [knowledgeBaseData, chunkData] = await Promise.all([
          getKnowledgeBase(knowledgeItemData.knowledge_base_id),
          getKnowledgeItemChunks(id),
        ]);

        setKnowledgeItem(knowledgeItemData);
        setKnowledgeBase(knowledgeBaseData);
        setChunks(chunkData);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "知识条目详情加载失败");
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, [knowledgeItemId]);

  async function handleSave(values: KnowledgeItemPayload) {
    if (knowledgeItemId === null) {
      return;
    }

    try {
      setSaving(true);
      setError("");
      setSuccessMessage("");
      const updated = await updateKnowledgeItem(knowledgeItemId, values);
      setKnowledgeItem(updated);
      setEditOpened(false);
      setSuccessMessage("知识条目已更新");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目更新失败");
    } finally {
      setSaving(false);
    }
  }

  async function reloadChunks(currentKnowledgeItemId: number) {
    const chunkData = await getKnowledgeItemChunks(currentKnowledgeItemId);
    setChunks(chunkData);
  }

  async function handleSplitChunks() {
    if (knowledgeItemId === null) {
      return;
    }

    try {
      setChunking(true);
      setError("");
      setSuccessMessage("");
      setProcessingResult(null);

      const result = await splitKnowledgeItemIntoChunks(knowledgeItemId);
      await reloadChunks(knowledgeItemId);
      setProcessingResult(result);
      setSuccessMessage("知识条目已完成切分");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目切分失败");
    } finally {
      setChunking(false);
    }
  }

  async function handleIndex() {
    if (knowledgeItemId === null) {
      return;
    }

    try {
      setIndexing(true);
      setError("");
      setSuccessMessage("");
      setProcessingResult(null);

      const result = await indexKnowledgeItem(knowledgeItemId);
      await reloadChunks(knowledgeItemId);
      setProcessingResult(result);
      setSuccessMessage("知识条目已完成索引");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目索引失败");
    } finally {
      setIndexing(false);
    }
  }

  async function handleDelete() {
    if (knowledgeItemId === null || !knowledgeItem) {
      return;
    }

    try {
      setDeleting(true);
      setError("");
      await deleteKnowledgeItem(knowledgeItemId);
      router.push(`/knowledge-bases/${knowledgeItem.knowledge_base_id}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "知识条目删除失败");
    } finally {
      setDeleting(false);
      setDeleteOpened(false);
    }
  }

  return (
    <Stack gap="lg">
      <PageHeader
        title={knowledgeItem ? knowledgeItem.title : "知识条目详情"}
        description="这里聚焦单条知识：来源、状态、正文，以及这个知识条目切出来的 chunks。"
        rightSection={
          <Group>
            {knowledgeBase ? (
              <Button
                component={Link}
                href={`/knowledge-bases/${knowledgeBase.id}`}
                variant="default"
                leftSection={<IconArrowLeft size={16} />}
              >
                返回知识库
              </Button>
            ) : null}
            <Button
              variant="light"
              leftSection={<IconEdit size={16} />}
              onClick={() => setEditOpened(true)}
              disabled={!knowledgeItem}
            >
              编辑条目
            </Button>
            <Button
              variant="default"
              onClick={() => void handleSplitChunks()}
              loading={chunking}
              disabled={!knowledgeItem || indexing}
            >
              生成 Chunks
            </Button>
            <Button
              onClick={() => void handleIndex()}
              loading={indexing}
              disabled={!knowledgeItem || chunking}
            >
              构建索引
            </Button>
          </Group>
        }
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="加载失败">
          {error}
        </Alert>
      ) : null}

      {successMessage ? (
        <Alert color="green" title="保存成功">
          {successMessage}
        </Alert>
      ) : null}

      {processingResult ? (
        <Alert color="blue" title="最近一次处理结果">
          {"vector_count" in processingResult
            ? `知识条目 ${processingResult.knowledge_item_id} 已写入 ${processingResult.vector_count} 条向量，chunk 数 ${processingResult.chunk_count}，索引名 ${processingResult.index_name}。`
            : `知识条目 ${processingResult.knowledge_item_id} 已生成 ${processingResult.chunk_count} 个 chunk。`}
        </Alert>
      ) : null}

      {loading ? (
        <Loader size="sm" />
      ) : knowledgeItem ? (
        <Grid gutter="md">
          <Grid.Col span={{ base: 12, lg: 8 }}>
            <Card withBorder radius="sm" padding="lg">
              <Stack gap="md">
                <Group justify="space-between">
                  <Text fw={700}>正文</Text>
                  <Group gap="xs">
                    <Badge
                      variant="light"
                      color={knowledgeItem.status === "active" ? "teal" : "yellow"}
                    >
                      {knowledgeItem.status}
                    </Badge>
                    <Badge
                      variant="light"
                      color={
                        knowledgeItem.source_type === "document" ? "blue" : "violet"
                      }
                    >
                      {knowledgeItem.source_type}
                    </Badge>
                  </Group>
                </Group>
                <Text size="sm" c="dimmed">
                  标签：{knowledgeItem.tags || "-"}
                </Text>
                <Code block>{knowledgeItem.content || "暂无正文"}</Code>
              </Stack>
            </Card>
          </Grid.Col>
          <Grid.Col span={{ base: 12, lg: 4 }}>
            <Stack gap="md">
              <Card withBorder radius="sm" padding="lg">
                <Stack gap="sm">
                  <Text fw={700}>概览</Text>
                  <Text size="sm" c="dimmed">
                    条目 ID：{knowledgeItem.id}
                  </Text>
                  <Text size="sm" c="dimmed">
                    知识库：{knowledgeBase?.name ?? knowledgeItem.knowledge_base_id}
                  </Text>
                  <Text size="sm" c="dimmed">
                    来源文档 ID：{knowledgeItem.source_document_id ?? "-"}
                  </Text>
                  <Text size="sm" c="dimmed">
                    Chunk 数：{chunks.length}
                  </Text>
                  <Text size="sm" c="dimmed">
                    当前索引状态：{chunks.some((chunk) => chunk.vector_id) ? "已生成向量" : "未索引"}
                  </Text>
                </Stack>
              </Card>
              <Card withBorder radius="sm" padding="lg">
                <Stack gap="sm">
                  <Text fw={700}>危险操作</Text>
                  <Text size="sm" c="dimmed">
                    删除条目会让这条知识本身不可见。如果它来自文档，文档记录和原文件不会因此自动删除。
                  </Text>
                  <Button
                    color="red"
                    variant="light"
                    leftSection={<IconTrash size={16} />}
                    onClick={() => setDeleteOpened(true)}
                  >
                    删除知识条目
                  </Button>
                </Stack>
              </Card>
            </Stack>
          </Grid.Col>
        </Grid>
      ) : null}

      <Card withBorder radius="sm" padding="lg">
        <Stack gap="md">
          <Group justify="space-between">
            <Text fw={700}>Chunks</Text>
            <Badge variant="light">{chunks.length} 条</Badge>
          </Group>

          {chunks.length === 0 ? (
            <Text c="dimmed" size="sm">
              当前这条知识还没有切出 chunk。手动条目目前不会自动切 chunk，文档来源条目通常会在执行索引后出现。
            </Text>
          ) : (
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Chunk</Table.Th>
                  <Table.Th>内容</Table.Th>
                  <Table.Th>vector_id</Table.Th>
                  <Table.Th>metadata</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {chunks.map((chunk) => {
                  const metadataEntry = parsedChunkMetadata.find(
                    (entry) => entry.chunkId === chunk.id,
                  );

                  return (
                    <Table.Tr key={chunk.id}>
                      <Table.Td>
                        <Stack gap={2}>
                          <Text size="sm" fw={600}>
                            #{chunk.chunk_index}
                          </Text>
                          <Text size="xs" c="dimmed">
                            id={chunk.id}
                          </Text>
                        </Stack>
                      </Table.Td>
                      <Table.Td maw={420}>
                        <Text size="sm" lineClamp={4}>
                          {chunk.content}
                        </Text>
                      </Table.Td>
                      <Table.Td maw={260}>
                        <Code block>{chunk.vector_id ?? "-"}</Code>
                      </Table.Td>
                      <Table.Td maw={360}>
                        <Code block>
                          {JSON.stringify(metadataEntry?.metadata ?? chunk.metadata_json, null, 2)}
                        </Code>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Card>

      <Modal
        opened={editOpened}
        onClose={() => setEditOpened(false)}
        title="编辑知识条目"
        centered
        size="lg"
      >
        {knowledgeItem ? (
          <KnowledgeItemForm
            initialValues={{
              knowledge_base_id: knowledgeItem.knowledge_base_id,
              title: knowledgeItem.title,
              content: knowledgeItem.content,
              tags: knowledgeItem.tags,
              status: knowledgeItem.status,
            }}
            loading={saving}
            submitLabel="保存条目"
            onSubmit={handleSave}
            onCancel={() => setEditOpened(false)}
          />
        ) : null}
      </Modal>

      <KnowledgeItemDeleteModal
        knowledgeItem={knowledgeItem}
        opened={deleteOpened}
        loading={deleting}
        onClose={() => setDeleteOpened(false)}
        onConfirm={handleDelete}
      />
    </Stack>
  );
}
