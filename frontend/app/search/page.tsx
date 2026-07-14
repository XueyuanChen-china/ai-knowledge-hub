"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Divider,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconDatabaseSearch,
  IconSearch,
} from "@tabler/icons-react";

import { PageHeader } from "@/components/page-header";
import {
  ApiError,
  getKnowledgeBases,
  searchSemantic,
} from "@/lib/api/client";
import type { KnowledgeBase, SemanticSearchResult } from "@/lib/api/types";

function formatScore(score: number) {
  return score.toFixed(4);
}

function buildMetadataSummary(metadata: Record<string, unknown>) {
  const entries: Array<[string, unknown]> = [];
  const candidateKeys = [
    "file_type",
    "filename",
    "heading_path",
    "page_start",
    "page_end",
    "sheet_name",
    "block_type",
    "chunk_index",
  ];

  for (const key of candidateKeys) {
    if (metadata[key] !== undefined && metadata[key] !== null && metadata[key] !== "") {
      entries.push([key, metadata[key]]);
    }
  }

  return entries;
}

export default function SearchPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] =
    useState<string>("");
  const [query, setQuery] = useState("采购复核");
  const [topK, setTopK] = useState<number>(5);
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);
  const [error, setError] = useState("");

  const selectedKnowledgeBase = useMemo(
    () =>
      knowledgeBases.find(
        (knowledgeBase) => String(knowledgeBase.id) === selectedKnowledgeBaseId,
      ) ?? null,
    [knowledgeBases, selectedKnowledgeBaseId],
  );

  useEffect(() => {
    async function loadKnowledgeBasesList() {
      try {
        setInitLoading(true);
        setError("");
        const data = await getKnowledgeBases();
        setKnowledgeBases(data);
        if (data[0]) {
          setSelectedKnowledgeBaseId(String(data[0].id));
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "知识库加载失败");
      } finally {
        setInitLoading(false);
      }
    }

    void loadKnowledgeBasesList();
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedKnowledgeBaseId) {
      setError("请先选择知识库");
      return;
    }

    if (!query.trim()) {
      setError("请输入搜索问题");
      return;
    }

    try {
      setLoading(true);
      setError("");
      const data = await searchSemantic({
        knowledge_base_id: Number(selectedKnowledgeBaseId),
        query: query.trim(),
        top_k: topK,
      });
      setResults(data);
    } catch (err) {
      setResults([]);
      setError(err instanceof ApiError ? err.message : "语义搜索失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Stack gap="lg">
      <PageHeader
        title="语义搜索"
        description="像搜索引擎一样先查知识库。第一版先把 query、知识库选择、Top K 和结果列表接完整。"
      />

      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="请求失败">
          {error}
        </Alert>
      ) : null}

      <Card withBorder padding="lg" radius="sm">
        {initLoading ? (
          <Loader size="sm" />
        ) : (
          <form onSubmit={handleSubmit}>
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
                  onChange={(value) => setSelectedKnowledgeBaseId(value ?? "")}
                  searchable
                  leftSection={<IconDatabaseSearch size={16} />}
                />
                <NumberInput
                  label="Top K"
                  min={1}
                  max={20}
                  value={topK}
                  onChange={(value) => setTopK(Number(value) || 5)}
                />
              </Group>

              <TextInput
                label="搜索问题"
                placeholder="例如：采购复核的触发条件是什么？"
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
              />

              <Group justify="space-between">
                <Text size="sm" c="dimmed">
                  当前知识库：{selectedKnowledgeBase?.name ?? "未选择"}
                </Text>
                <Button
                  type="submit"
                  leftSection={<IconSearch size={16} />}
                  loading={loading}
                >
                  开始搜索
                </Button>
              </Group>
            </Stack>
          </form>
        )}
      </Card>

      <Card withBorder padding="lg" radius="sm">
        <Stack gap="md">
          <Group justify="space-between">
            <div>
              <Text fw={700}>搜索结果</Text>
              <Text size="sm" c="dimmed">
                共返回 {results.length} 条结果，按后端语义分数排序。
              </Text>
            </div>
            {results.length > 0 ? (
              <Badge variant="light" color="blue">
                Top {results.length}
              </Badge>
            ) : null}
          </Group>

          {loading ? (
            <Loader size="sm" />
          ) : results.length === 0 ? (
            <Text c="dimmed" size="sm">
              还没有搜索结果。
            </Text>
          ) : (
            <Stack gap="md">
              {results.map((item, index) => {
                const metadataSummary = buildMetadataSummary(item.metadata);

                return (
                  <Card key={`${item.chunk_id ?? "none"}-${index}`} withBorder radius="sm">
                    <Stack gap="sm">
                      <Group justify="space-between" align="flex-start">
                        <div>
                          <Text fw={700}>{item.title}</Text>
                          <Text size="sm" c="dimmed">
                            doc_id={item.doc_id ?? "-"} | chunk_id={item.chunk_id ?? "-"}
                          </Text>
                        </div>
                        <Badge color="teal" variant="light">
                          score {formatScore(item.score)}
                        </Badge>
                      </Group>

                      <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                        {item.content_preview}
                      </Text>

                      {metadataSummary.length > 0 ? (
                        <>
                          <Divider />
                          <Stack gap={6}>
                            <Text size="sm" fw={600}>
                              Metadata
                            </Text>
                            {metadataSummary.map(([key, value]) => (
                              <Code key={key} block>
                                {key}:{" "}
                                {typeof value === "string"
                                  ? value
                                  : JSON.stringify(value, null, 2)}
                              </Code>
                            ))}
                          </Stack>
                        </>
                      ) : null}
                    </Stack>
                  </Card>
                );
              })}
            </Stack>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
