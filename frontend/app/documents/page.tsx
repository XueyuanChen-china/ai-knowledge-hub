"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  FileInput,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconDatabase,
  IconRefresh,
  IconUpload,
} from "@tabler/icons-react";

import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  getDocuments,
  getKnowledgeBases,
  getOssUploadTask,
  uploadDocumentToOss,
} from "@/lib/api/client";
import type {
  DocumentRecord,
  KnowledgeBase,
} from "@/lib/api/types";

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
  });
}

function getStatusColor(status: DocumentRecord["status"]) {
  if (status === "indexed") {
    return "teal";
  }
  if (status === "failed") {
    return "red";
  }
  return "yellow";
}

function getStatusLabel(status: DocumentRecord["status"]) {
  if (status === "indexed") {
    return "已索引";
  }
  if (status === "failed") {
    return "失败";
  }
  return "待索引";
}

export default function DocumentsPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] =
    useState<string>("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string>("");
  const [error, setError] = useState("");

  const selectedKnowledgeBase = useMemo(
    () =>
      knowledgeBases.find(
        (knowledgeBase) => String(knowledgeBase.id) === selectedKnowledgeBaseId,
      ) ?? null,
    [knowledgeBases, selectedKnowledgeBaseId],
  );

  const loadKnowledgeBasesList = useCallback(async () => {
    const data = await getKnowledgeBases();
    setKnowledgeBases(data);
    return data;
  }, []);

  const loadDocumentsList = useCallback(async (knowledgeBaseId?: number) => {
    setDocuments(await getDocuments(knowledgeBaseId));
  }, []);

  const initializePage = useCallback(async () => {
    try {
      setLoading(true);
      setError("");
      const data = await loadKnowledgeBasesList();
      const initialKnowledgeBaseId = data[0] ? String(data[0].id) : "";

      if (initialKnowledgeBaseId) {
        setSelectedKnowledgeBaseId(initialKnowledgeBaseId);
        await loadDocumentsList(Number(initialKnowledgeBaseId));
      } else {
        setDocuments([]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "文档页面初始化失败");
    } finally {
      setLoading(false);
    }
  }, [loadDocumentsList, loadKnowledgeBasesList]);

  useEffect(() => {
    // 首次进入时同时拉知识库和文档列表。
    void initializePage();
  }, [initializePage]);

  async function handleKnowledgeBaseChange(value: string | null) {
    const nextValue = value ?? "";
    setSelectedKnowledgeBaseId(nextValue);

    if (!nextValue) {
      setDocuments([]);
      return;
    }

    try {
      setLoading(true);
      setError("");
      await loadDocumentsList(Number(nextValue));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "文档列表加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleUpload() {
    if (!selectedKnowledgeBaseId) {
      setError("请先选择知识库");
      return;
    }
    if (!selectedFile) {
      setError("请先选择文件");
      return;
    }

    try {
      setUploading(true);
      setError("");
      setUploadProgress("正在初始化 OSS 上传任务...");
      const completed = await uploadDocumentToOss({
        knowledgeBaseId: Number(selectedKnowledgeBaseId),
        file: selectedFile,
        onProgress: (uploadedParts, totalParts) => {
          setUploadProgress(`正在上传分片 ${uploadedParts}/${totalParts}...`);
        },
      });
      setUploadProgress("文件已合并，正在等待解析和索引...");
      let task = await getOssUploadTask(completed.upload_id);
      const deadline = Date.now() + 10 * 60 * 1000;
      while (
        task.processing_status === "pending" ||
        task.processing_status === "running"
      ) {
        if (Date.now() >= deadline) {
          throw new Error("文件处理超时，请稍后刷新文档列表查看状态");
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
        task = await getOssUploadTask(completed.upload_id);
      }
      if (task.processing_status === "failed") {
        throw new Error(
          task.processing_error_message || "文件解析或索引失败",
        );
      }
      await loadDocumentsList(Number(selectedKnowledgeBaseId));
      setSelectedFile(null);
      setUploadProgress("上传、解析、切片和索引已完成");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "文档上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function handleRefresh() {
    if (!selectedKnowledgeBaseId) {
      return;
    }

    try {
      setLoading(true);
      setError("");
      await loadDocumentsList(Number(selectedKnowledgeBaseId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "文档列表刷新失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Stack gap="lg">
      <PageHeader
        title="文档上传与索引"
        description="上传文档后自动完成解析、切片、Embedding 和索引，并在列表中查看处理状态。"
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="请求失败">
          {error}
        </Alert>
      ) : null}

      <Card withBorder padding="lg" radius="sm">
        <Stack gap="md">
          <Group grow align="flex-end">
            <Select
              label="知识库"
              placeholder="请选择知识库"
              data={knowledgeBases.map((knowledgeBase) => ({
                value: String(knowledgeBase.id),
                label: `${knowledgeBase.id} - ${knowledgeBase.name}`,
              }))}
              value={selectedKnowledgeBaseId}
              onChange={handleKnowledgeBaseChange}
              searchable
              leftSection={<IconDatabase size={16} />}
            />
            <FileInput
              label="上传文件"
              placeholder="支持 txt / md / pdf / docx / xlsx"
              value={selectedFile}
              onChange={setSelectedFile}
              accept=".txt,.md,.pdf,.docx,.xlsx"
              clearable
            />
            <Button
              leftSection={<IconUpload size={16} />}
              loading={uploading}
              onClick={handleUpload}
            >
              上传文档
            </Button>
          </Group>

          <Text size="sm" c="dimmed">
            当前知识库：{selectedKnowledgeBase?.name ?? "未选择"}。文件会先直传阿里云 OSS，
            上传完成后自动进入解析、切片、Embedding 和索引流程。
            {uploadProgress ? ` ${uploadProgress}` : ""}
          </Text>
        </Stack>
      </Card>

      <Card withBorder padding="lg" radius="sm">
        <Stack gap="md">
          <Group justify="space-between">
            <div>
              <Text fw={700}>文档列表</Text>
              <Text size="sm" c="dimmed">
                当前按知识库过滤展示。
              </Text>
            </div>
            <ActionIcon
              variant="light"
              size="lg"
              onClick={() => void handleRefresh()}
              disabled={!selectedKnowledgeBaseId}
            >
              <IconRefresh size={18} />
            </ActionIcon>
          </Group>

          {loading ? (
            <Loader size="sm" />
          ) : documents.length === 0 ? (
            <Text c="dimmed" size="sm">
              当前知识库还没有上传文档。
            </Text>
          ) : (
            <Table striped highlightOnHover withTableBorder withColumnBorders>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>ID</Table.Th>
                  <Table.Th>文件名</Table.Th>
                  <Table.Th>类型</Table.Th>
                  <Table.Th>状态</Table.Th>
                  <Table.Th>提取文本</Table.Th>
                  <Table.Th>上传时间</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {documents.map((document) => (
                  <Table.Tr key={document.id}>
                    <Table.Td>{document.id}</Table.Td>
                    <Table.Td>{document.filename}</Table.Td>
                    <Table.Td>{document.file_type.toUpperCase()}</Table.Td>
                    <Table.Td>
                      <Badge color={getStatusColor(document.status)} variant="light">
                        {getStatusLabel(document.status)}
                      </Badge>
                    </Table.Td>
                    <Table.Td maw={420}>
                      <Text size="sm" lineClamp={3}>
                        {document.extracted_text || "暂无提取文本"}
                      </Text>
                    </Table.Td>
                    <Table.Td>{formatTime(document.created_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
