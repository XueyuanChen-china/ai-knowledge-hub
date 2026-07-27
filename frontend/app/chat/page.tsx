"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  Menu,
  Modal,
  NumberInput,
  Paper,
  ScrollArea,
  Select,
  Stack,
  Tabs,
  Text,
  TextInput,
  Textarea,
  ThemeIcon,
  Timeline,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconChecks,
  IconDotsVertical,
  IconHistory,
  IconMessageCircle,
  IconPencil,
  IconPin,
  IconPlayerPause,
  IconPlus,
  IconRefresh,
  IconRobot,
  IconSend,
  IconTrash,
  IconUser,
  IconX,
} from "@tabler/icons-react";

import {
  ApiError,
  deleteConversation,
  getConversationMessages,
  getConversations,
  getKnowledgeBases,
  streamChat,
  streamResumeChat,
  updateConversation,
} from "@/lib/api/client";
import type {
  ChatCitation,
  ChatStreamErrorEvent,
  ChatStreamNodeEvent,
  ChatStreamStartEvent,
  ChatRunResponse,
  ConversationMessage,
  ConversationSummary,
  KnowledgeBase,
  RetrievedDocPreviewItem,
} from "@/lib/api/types";

type UiMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  citations?: ChatCitation[];
};

type EvidenceTab = "process" | "evidence" | "citations";

type LiveRunState = {
  status: "idle" | "streaming" | "interrupted" | "completed";
  currentNode: string;
  nodeTrace: string[];
  route: string;
  routeReason: string;
  retrievalHitCount: number;
  relevanceDecision: string;
  reviewReason: string;
  threadId: string | null;
  conversationId: number | null;
  docsPreview: string;
  retrievedDocsPreviewItems: RetrievedDocPreviewItem[];
};

const EMPTY_LIVE_RUN_STATE: LiveRunState = {
  status: "idle",
  currentNode: "",
  nodeTrace: [],
  route: "",
  routeReason: "",
  retrievalHitCount: 0,
  relevanceDecision: "",
  reviewReason: "",
  threadId: null,
  conversationId: null,
  docsPreview: "",
  retrievedDocsPreviewItems: [],
};

function shortThreadId(threadId: string | null) {
  if (!threadId) {
    return "-";
  }
  return `${threadId.slice(0, 8)}...${threadId.slice(-6)}`;
}

function formatScore(score: number) {
  return score.toFixed(4);
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    hour12: false,
  });
}

function mapConversationMessageToUiMessage(
  message: ConversationMessage,
): UiMessage {
  return {
    id: `history-${message.id}`,
    role:
      message.role === "assistant" || message.role === "system"
        ? message.role
        : "user",
    content: message.content,
    citations: message.citations,
  };
}

function getStatusColor(status: string, pendingReview: boolean) {
  if (pendingReview) {
    return "yellow";
  }
  if (status === "completed") {
    return "teal";
  }
  if (status === "streaming") {
    return "blue";
  }
  return "gray";
}

function renderCitationCards(citations: ChatCitation[]) {
  if (!citations.length) {
    return null;
  }

  return (
    <>
      <Divider />
      <Stack gap="xs">
        <Text size="xs" fw={700} tt="uppercase" c="dimmed">
          引用来源 · {citations.length}
        </Text>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 8,
          }}
        >
          {citations.map((citation, index) => (
            <Paper
              key={`${citation.chunk_id}-${index}`}
              withBorder
              p="sm"
              radius="sm"
              style={{ background: "#fbfdff" }}
            >
              <Stack gap={4}>
                <Text size="sm" fw={650} lineClamp={1}>
                  {citation.title}
                </Text>
                <Text size="xs" c="dimmed">
                  doc_id={citation.doc_id} | chunk_id={citation.chunk_id}
                </Text>
                <Badge variant="light" color="blue" size="xs">
                  score {formatScore(citation.score)}
                </Badge>
              </Stack>
            </Paper>
          ))}
        </div>
      </Stack>
    </>
  );
}

function ChatMessageCard({ message }: { message: UiMessage }) {
  if (message.role === "user") {
    return (
      <Group justify="flex-end" align="flex-start">
        <Paper
          radius="sm"
          p="md"
          maw="78%"
          style={{ background: "#1769aa", color: "white" }}
        >
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {message.content}
          </Text>
        </Paper>
        <ThemeIcon color="blue" variant="light" size={30}>
          <IconUser size={16} />
        </ThemeIcon>
      </Group>
    );
  }

  if (message.role === "system") {
    return (
      <Alert color="yellow" variant="light" radius="sm" icon={<IconPlayerPause size={16} />}>
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
          {message.content}
        </Text>
      </Alert>
    );
  }

  return (
    <Group align="flex-start" gap="sm" wrap="nowrap">
      <ThemeIcon color="teal" variant="light" size={30}>
        <IconRobot size={16} />
      </ThemeIcon>
      <Paper withBorder radius="sm" p="md" maw="88%" bg="white">
        <Stack gap="sm">
          <Group gap="xs">
            <Text fw={700} size="sm">
              专家 Agent
            </Text>
            <Badge variant="light" color="teal" size="xs">
              answer
            </Badge>
          </Group>
          <Text size="sm" style={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
            {message.content}
          </Text>
          {message.citations ? renderCitationCards(message.citations) : null}
        </Stack>
      </Paper>
    </Group>
  );
}

function ReviewInlineCard({
  pendingReview,
  retrievedDocsPreviewItems,
  reviewNote,
  setReviewNote,
  resumeAction,
  onResume,
}: {
  pendingReview: ChatRunResponse;
  retrievedDocsPreviewItems: RetrievedDocPreviewItem[];
  reviewNote: string;
  setReviewNote: (value: string) => void;
  resumeAction: "approve" | "reject" | null;
  onResume: (approved: boolean) => void;
}) {
  const docsPreview =
    pendingReview.review_payload?.docs_preview ||
    pendingReview.docs_preview ||
    "暂无检索预览";
  const docsPreviewLines = docsPreview.split("\n").filter(Boolean);

  return (
    <Card withBorder radius="sm" padding={0} style={{ borderColor: "#f2c879" }}>
      <Group
        justify="space-between"
        p="md"
        style={{ borderBottom: "1px solid #f2c879", background: "#fff8e6" }}
      >
        <Group gap="xs">
          <ThemeIcon color="yellow" variant="light" size={30}>
            <IconPlayerPause size={16} />
          </ThemeIcon>
          <div>
            <Text fw={700}>需要人工审核</Text>
            <Text size="xs" c="dimmed">
              当前问题已暂停，确认后继续生成或结束流程。
            </Text>
          </div>
        </Group>
        <Badge variant="light" color="yellow">
          {pendingReview.relevance_decision || "need_review"}
        </Badge>
      </Group>

      <Stack gap="md" p="md">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) 210px",
            gap: 12,
          }}
        >
          <div>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase">
              审核原因
            </Text>
            <Text size="sm" mt={4}>
              {pendingReview.review_reason || "当前结果需要人工复核。"}
            </Text>
          </div>

          <Paper
            withBorder
            radius="sm"
            p="sm"
            style={{ borderColor: "#f2c879", background: "#fffdf5" }}
          >
            <Text size="xs" fw={700} c="yellow.8">
              用户问题
            </Text>
            <Text size="sm" mt={4}>
              {pendingReview.review_payload?.question || "未提供"}
            </Text>
          </Paper>
        </div>

        <Stack gap="xs">
          <Group justify="space-between">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase">
              候选证据
            </Text>
            <Badge variant="light" color="yellow" size="xs">
              命中 {pendingReview.retrieval_hit_count} 条
            </Badge>
          </Group>
          <ScrollArea h={150} offsetScrollbars type="hover">
            <Stack gap="xs" pr="xs">
              {retrievedDocsPreviewItems.length ? (
                retrievedDocsPreviewItems.map((item) => (
                  <Paper
                    key={`${item.chunk_id}-${item.index}`}
                    withBorder
                    radius="sm"
                    p="sm"
                    style={{ borderColor: "#f2c879", background: "#fffdf5" }}
                  >
                    <Stack gap={4}>
                      <Group justify="space-between" gap="xs" wrap="nowrap">
                        <Text size="xs" fw={700} lineClamp={1}>
                          [{item.index}] {item.title || "未命名来源"}
                        </Text>
                        <Badge size="xs" variant="light" color="yellow">
                          {formatScore(item.score)}
                        </Badge>
                      </Group>
                      <Text size="xs" c="dimmed">
                        doc_id={item.doc_id ?? "-"} | chunk_id={item.chunk_id ?? "-"}
                      </Text>
                      <Text size="xs" style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                        {item.content_preview || item.content || "暂无内容"}
                      </Text>
                    </Stack>
                  </Paper>
                ))
              ) : docsPreviewLines.length ? (
                docsPreviewLines.map((line, index) => (
                  <Paper
                    key={`${line}-${index}`}
                    withBorder
                    radius="sm"
                    p="sm"
                    style={{ borderColor: "#f2c879", background: "#fffdf5" }}
                  >
                    <Text size="xs" style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                      {line}
                    </Text>
                  </Paper>
                ))
              ) : (
                <Text size="xs" c="dimmed">
                  暂无检索预览
                </Text>
              )}
            </Stack>
          </ScrollArea>
        </Stack>
      </Stack>

      <Stack gap="sm" p="md" style={{ borderTop: "1px solid #f2c879" }}>
        <Textarea
          label="审核说明"
          minRows={2}
          value={reviewNote}
          onChange={(event) => setReviewNote(event.currentTarget.value)}
          placeholder="例如：证据不足，拒绝直接回答"
        />
        <Group justify="flex-end">
          <Button
            color="red"
            variant="light"
            leftSection={<IconX size={16} />}
            loading={resumeAction === "reject"}
            onClick={() => onResume(false)}
          >
            拒绝并结束
          </Button>
          <Button
            color="teal"
            leftSection={<IconChecks size={16} />}
            loading={resumeAction === "approve"}
            onClick={() => onResume(true)}
          >
            通过并继续生成
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

export default function ChatPage() {
  const streamMessageIdRef = useRef<string | null>(null);
  const streamAnswerTextRef = useRef("");
  const streamReferenceNumbersRef = useRef<number[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] =
    useState<string>("");
  const [selectedConversationId, setSelectedConversationId] = useState<
    number | null
  >(null);
  const [question, setQuestion] = useState("采购复核的触发条件是什么？");
  const [retrieveTopK, setRetrieveTopK] = useState<number>(5);
  const [loading, setLoading] = useState(false);
  const [initLoading, setInitLoading] = useState(true);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [resumeAction, setResumeAction] = useState<"approve" | "reject" | null>(
    null,
  );
  const [error, setError] = useState("");
  const [response, setResponse] = useState<ChatRunResponse | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [pendingReview, setPendingReview] = useState<ChatRunResponse | null>(
    null,
  );
  const [reviewNote, setReviewNote] = useState("");
  const [liveRunState, setLiveRunState] =
    useState<LiveRunState>(EMPTY_LIVE_RUN_STATE);
  const [streamLogs, setStreamLogs] = useState<string[]>([]);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("process");
  const [renameConversationTarget, setRenameConversationTarget] =
    useState<ConversationSummary | null>(null);
  const [renameConversationTitle, setRenameConversationTitle] = useState("");
  const [deleteConversationTarget, setDeleteConversationTarget] =
    useState<ConversationSummary | null>(null);
  const [conversationActionLoading, setConversationActionLoading] =
    useState(false);

  const selectedKnowledgeBase = useMemo(
    () =>
      knowledgeBases.find(
        (knowledgeBase) => String(knowledgeBase.id) === selectedKnowledgeBaseId,
      ) ?? null,
    [knowledgeBases, selectedKnowledgeBaseId],
  );

  const selectedConversation = useMemo(
    () =>
      conversations.find((conversation) => conversation.id === selectedConversationId) ??
      null,
    [conversations, selectedConversationId],
  );
  const effectiveStatus = pendingReview
    ? "interrupted"
    : loading
      ? liveRunState.status
      : response?.status || liveRunState.status;
  const effectiveRoute = response?.route || liveRunState.route || "-";
  const effectiveRouteReason =
    response?.route_reason || liveRunState.routeReason || "-";
  const effectiveRetrievalHitCount =
    response?.retrieval_hit_count ?? liveRunState.retrievalHitCount;
  const effectiveRelevanceDecision =
    response?.relevance_decision || liveRunState.relevanceDecision || "-";
  const effectiveReviewReason =
    response?.review_reason || liveRunState.reviewReason || "-";
  const effectiveNodeTrace =
    response?.node_trace?.length ? response.node_trace : liveRunState.nodeTrace;
  const effectiveDocsPreview =
    pendingReview?.review_payload?.docs_preview ||
    pendingReview?.docs_preview ||
    response?.docs_preview ||
    liveRunState.docsPreview ||
    "";
  const effectiveRetrievedDocsPreviewItems = useMemo(() => {
    if (pendingReview?.retrieved_docs_preview_items?.length) {
      return pendingReview.retrieved_docs_preview_items;
    }
    if (response?.retrieved_docs_preview_items?.length) {
      return response.retrieved_docs_preview_items;
    }
    return liveRunState.retrievedDocsPreviewItems;
  }, [
    liveRunState.retrievedDocsPreviewItems,
    pendingReview?.retrieved_docs_preview_items,
    response?.retrieved_docs_preview_items,
  ]);
  const docsPreviewLines = useMemo(
    () => effectiveDocsPreview.split("\n").filter(Boolean),
    [effectiveDocsPreview],
  );
  const latestCitations = useMemo(() => {
    if (response?.citations.length) {
      return response.citations;
    }

    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const citations = messages[index].citations;
      if (citations?.length) {
        return citations;
      }
    }

    return [];
  }, [messages, response]);

  const loadConversationList = useCallback(async (knowledgeBaseId?: number) => {
    setConversationLoading(true);
    try {
      const data = await getConversations(knowledgeBaseId);
      setConversations(data);
      return data;
    } finally {
      setConversationLoading(false);
    }
  }, []);

  useEffect(() => {
    async function initializePage() {
      try {
        setInitLoading(true);
        setError("");
        const data = await getKnowledgeBases();
        setKnowledgeBases(data);

        const initialKnowledgeBaseId = data[0] ? String(data[0].id) : "";
        setSelectedKnowledgeBaseId(initialKnowledgeBaseId);

        if (initialKnowledgeBaseId) {
          await loadConversationList(Number(initialKnowledgeBaseId));
        } else {
          setConversations([]);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "知识库加载失败");
      } finally {
        setInitLoading(false);
      }
    }

    void initializePage();
  }, [loadConversationList]);

  async function handleSelectConversation(conversation: ConversationSummary) {
    try {
      setHistoryLoading(true);
      setError("");
      const historyMessages = await getConversationMessages(conversation.id);
      setSelectedConversationId(conversation.id);
      setThreadId(conversation.thread_id);
      setPendingReview(null);
      setReviewNote("");
      setResponse(null);
      setLiveRunState({
        ...EMPTY_LIVE_RUN_STATE,
        threadId: conversation.thread_id,
        conversationId: conversation.id,
      });
      setStreamLogs([]);
      resetStreamMessage();
      setMessages(historyMessages.map(mapConversationMessageToUiMessage));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "历史消息加载失败");
    } finally {
      setHistoryLoading(false);
    }
  }

  function handleStartNewConversation() {
    setSelectedConversationId(null);
    setThreadId(null);
    setResponse(null);
    setMessages([]);
    setPendingReview(null);
    setReviewNote("");
    setQuestion("采购复核的触发条件是什么？");
    setLiveRunState(EMPTY_LIVE_RUN_STATE);
    setStreamLogs([]);
    resetStreamMessage();
    setError("");
  }

  function openRenameConversationModal(conversation: ConversationSummary) {
    setRenameConversationTarget(conversation);
    setRenameConversationTitle(conversation.title);
  }

  async function handleRenameConversation() {
    if (!renameConversationTarget) {
      return;
    }

    const title = renameConversationTitle.trim();
    if (!title) {
      setError("会话标题不能为空");
      return;
    }

    try {
      setConversationActionLoading(true);
      setError("");
      const updatedConversation = await updateConversation(
        renameConversationTarget.id,
        { title },
      );
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === updatedConversation.id
            ? updatedConversation
            : conversation,
        ),
      );
      setRenameConversationTarget(null);
      setRenameConversationTitle("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "会话重命名失败");
    } finally {
      setConversationActionLoading(false);
    }
  }

  async function handleTogglePinConversation(conversation: ConversationSummary) {
    try {
      setConversationActionLoading(true);
      setError("");
      await updateConversation(conversation.id, {
        is_pinned: !conversation.is_pinned,
      });
      await loadConversationList(
        selectedKnowledgeBaseId ? Number(selectedKnowledgeBaseId) : undefined,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "会话置顶状态更新失败");
    } finally {
      setConversationActionLoading(false);
    }
  }

  async function handleDeleteConversation() {
    if (!deleteConversationTarget) {
      return;
    }

    try {
      setConversationActionLoading(true);
      setError("");
      await deleteConversation(deleteConversationTarget.id);
      setConversations((current) =>
        current.filter(
          (conversation) => conversation.id !== deleteConversationTarget.id,
        ),
      );
      if (selectedConversationId === deleteConversationTarget.id) {
        handleStartNewConversation();
      }
      setDeleteConversationTarget(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "会话删除失败");
    } finally {
      setConversationActionLoading(false);
    }
  }

  function appendStreamLog(message: string) {
    setStreamLogs((current) => [
      ...current.slice(-19),
      `${new Date().toLocaleTimeString("zh-CN", { hour12: false })} ${message}`,
    ]);
  }

  function resetStreamMessage() {
    streamMessageIdRef.current = null;
    streamAnswerTextRef.current = "";
    streamReferenceNumbersRef.current = [];
  }

  function upsertStreamMessage(
    role: UiMessage["role"],
    content: string,
    citations?: ChatCitation[],
  ) {
    const streamMessageId = streamMessageIdRef.current ?? `stream-${Date.now()}`;
    streamMessageIdRef.current = streamMessageId;

    setMessages((current) => {
      const nextMessage: UiMessage = {
        id: streamMessageId,
        role,
        content,
        citations,
      };
      const index = current.findIndex((message) => message.id === streamMessageId);
      if (index === -1) {
        return [...current, nextMessage];
      }
      return current.map((message) =>
        message.id === streamMessageId ? nextMessage : message,
      );
    });
  }

  function removeStreamMessage() {
    const streamMessageId = streamMessageIdRef.current;
    if (!streamMessageId) {
      return;
    }

    setMessages((current) =>
      current.filter((message) => message.id !== streamMessageId),
    );
    streamMessageIdRef.current = null;
    streamAnswerTextRef.current = "";
    streamReferenceNumbersRef.current = [];
  }

  function appendAnswerChunk(answerChunk: string) {
    streamAnswerTextRef.current += answerChunk;
    upsertStreamMessage("assistant", streamAnswerTextRef.current);
  }

  function applyReferenceNumbers(referenceNumbers: number[]) {
    streamReferenceNumbersRef.current = referenceNumbers;
    const referenceText =
      referenceNumbers.length > 0
        ? `\n\n引用：${referenceNumbers.map((number) => `[${number}]`).join("")}`
        : "";
    upsertStreamMessage(
      "assistant",
      `${streamAnswerTextRef.current}${referenceText}`,
    );
  }

  function buildNodeProgressText(event: ChatStreamNodeEvent) {
    if (event.node === "router") {
      return `正在路由问题...\nroute=${event.route || "-"}\n原因：${event.route_reason || "-"}`;
    }
    if (event.node === "retrieve") {
      return `正在检索知识库...\n当前命中 ${event.retrieval_hit_count ?? 0} 条候选内容`;
    }
    if (event.node === "relevance_check") {
      return `正在做相关性判断...\n决策：${event.relevance_decision || "-"}\n原因：${event.review_reason || "-"}`;
    }
    if (event.node === "human_review") {
      return "人工审核已通过，继续执行后续节点...";
    }
    if (event.node === "answer") {
      return "正在生成最终答案...";
    }
    if (event.node === "review_rejected") {
      return `人工审核未通过：${event.review_reason || event.human_note || "流程结束"}`;
    }
    return `正在执行节点：${event.node}`;
  }

  function handleStartEvent(event: ChatStreamStartEvent) {
    setThreadId(event.thread_id);
    setLiveRunState((current) => ({
      ...current,
      status: "streaming",
      threadId: event.thread_id,
      conversationId: event.conversation_id ?? null,
    }));
    appendStreamLog("已创建流式会话，开始执行工作流");
    upsertStreamMessage("assistant", "已接收问题，开始执行工作流...");
  }

  function handleNodeEvent(event: ChatStreamNodeEvent) {
    setLiveRunState((current) => ({
      ...current,
      status: "streaming",
      currentNode: event.node,
      nodeTrace: event.node_trace ?? current.nodeTrace,
      route: event.route ?? current.route,
      routeReason: event.route_reason ?? current.routeReason,
      retrievalHitCount:
        event.retrieval_hit_count ?? current.retrievalHitCount,
      relevanceDecision:
        event.relevance_decision ?? current.relevanceDecision,
      reviewReason: event.review_reason ?? current.reviewReason,
      docsPreview: event.docs_preview ?? current.docsPreview,
      retrievedDocsPreviewItems:
        event.retrieved_docs_preview_items ?? current.retrievedDocsPreviewItems,
    }));

    if (event.node === "router") {
      appendStreamLog(
        `路由完成：${event.route || "-"}${event.route_reason ? `，${event.route_reason}` : ""}`,
      );
    } else if (event.node === "retrieve") {
      appendStreamLog(`检索完成：命中 ${event.retrieval_hit_count ?? 0} 条`);
    } else if (event.node === "relevance_check") {
      appendStreamLog(
        `相关性判定：${event.relevance_decision || "-"}${event.review_reason ? `，${event.review_reason}` : ""}`,
      );
    } else if (event.node === "human_review") {
      appendStreamLog("人工审核已恢复，继续执行后续节点");
    } else if (event.node === "answer") {
      appendStreamLog("答案节点完成，等待最终结果落库");
    } else if (event.node === "review_rejected") {
      appendStreamLog("人工审核拒绝，流程结束");
    }

    upsertStreamMessage("assistant", buildNodeProgressText(event));
  }

  function handleTerminalEvent(result: ChatRunResponse) {
    setResponse(result);
    setThreadId(result.thread_id);
    setLiveRunState({
      status: result.status === "interrupted" ? "interrupted" : "completed",
      currentNode: "",
      nodeTrace: result.node_trace,
      route: result.route,
      routeReason: result.route_reason,
      retrievalHitCount: result.retrieval_hit_count,
      relevanceDecision: result.relevance_decision,
      reviewReason: result.review_reason,
      threadId: result.thread_id,
      conversationId: result.conversation_id,
      docsPreview: result.docs_preview,
      retrievedDocsPreviewItems: result.retrieved_docs_preview_items ?? [],
    });
  }

  async function handleKnowledgeBaseChange(value: string | null) {
    const nextValue = value ?? "";
    setSelectedKnowledgeBaseId(nextValue);
    handleStartNewConversation();

    if (!nextValue) {
      setConversations([]);
      return;
    }

    try {
      setError("");
      await loadConversationList(Number(nextValue));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "会话列表加载失败");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedKnowledgeBaseId) {
      setError("请先选择知识库");
      return;
    }
    if (!question.trim()) {
      setError("请输入问题");
      return;
    }
    if (pendingReview) {
      setError("当前会话有待审核问题，请先通过或拒绝审核");
      return;
    }

    const normalizedQuestion = question.trim();
    const userMessage: UiMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: normalizedQuestion,
    };

    try {
      setLoading(true);
      setError("");
      setResponse(null);
      setLiveRunState(EMPTY_LIVE_RUN_STATE);
      setStreamLogs([]);
      resetStreamMessage();
      setMessages((current) => [...current, userMessage]);
      setQuestion("");
      upsertStreamMessage("assistant", "正在准备执行...");
      await streamChat(
        {
          knowledge_base_id: Number(selectedKnowledgeBaseId),
          question: normalizedQuestion,
          thread_id: threadId ?? undefined,
          retrieve_top_k: retrieveTopK,
        },
        async (event, data) => {
          if (event === "start") {
            handleStartEvent(data as ChatStreamStartEvent);
            return;
          }

          if (event === "node") {
            handleNodeEvent(data as ChatStreamNodeEvent);
            return;
          }

          if (event === "answer") {
            const answerChunk = data as string;
            setLiveRunState((current) => ({
              ...current,
              status: "streaming",
              currentNode: "answer",
            }));
            appendAnswerChunk(answerChunk);
            return;
          }

          if (event === "references") {
            applyReferenceNumbers(data as number[]);
            return;
          }

          if (event === "interrupted") {
            const result = data as ChatRunResponse;
            handleTerminalEvent(result);
            setPendingReview(result);
            setReviewNote(result.review_reason || "");
            appendStreamLog("命中人工审核中断，等待人工处理");

            const refreshedConversations = await loadConversationList(
              Number(selectedKnowledgeBaseId),
            );
            if (result.conversation_id) {
              setSelectedConversationId(result.conversation_id);
            } else if (refreshedConversations[0]) {
              setSelectedConversationId(refreshedConversations[0].id);
            }

            return;
          }

          if (event === "completed") {
            const result = data as ChatRunResponse;
            handleTerminalEvent(result);
            setPendingReview(null);
            setReviewNote("");
            appendStreamLog("工作流执行完成，答案已落库");
            upsertStreamMessage(
              "assistant",
              result.answer || "当前没有生成答案。",
              result.citations,
            );
            resetStreamMessage();

            const refreshedConversations = await loadConversationList(
              Number(selectedKnowledgeBaseId),
            );
            if (result.conversation_id) {
              setSelectedConversationId(result.conversation_id);
            } else if (refreshedConversations[0]) {
              setSelectedConversationId(refreshedConversations[0].id);
            }

            return;
          }

          if (event === "error") {
            const errorEvent = data as ChatStreamErrorEvent;
            throw new ApiError(errorEvent.detail || "对话流执行失败", 500);
          }
        },
      );
    } catch (err) {
      setMessages((current) =>
        current.filter((message) => message.id !== userMessage.id),
      );
      removeStreamMessage();
      setQuestion(normalizedQuestion);
      setError(err instanceof ApiError ? err.message : "对话请求失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleResume(approved: boolean) {
    if (!pendingReview) {
      return;
    }

    try {
      setResumeAction(approved ? "approve" : "reject");
      setError("");
      upsertStreamMessage(
        "assistant",
        approved ? "人工审核通过，继续执行工作流..." : "人工审核拒绝，正在结束流程...",
      );
      await streamResumeChat(
        {
          thread_id: pendingReview.thread_id,
          approved,
          human_note: reviewNote.trim(),
          retrieve_top_k: retrieveTopK,
        },
        async (event, data) => {
          if (event === "start") {
            handleStartEvent(data as ChatStreamStartEvent);
            appendStreamLog(approved ? "审核通过，恢复工作流" : "审核拒绝，进入结束分支");
            return;
          }

          if (event === "node") {
            handleNodeEvent(data as ChatStreamNodeEvent);
            return;
          }

          if (event === "answer") {
            const answerChunk = data as string;
            setLiveRunState((current) => ({
              ...current,
              status: "streaming",
              currentNode: "answer",
            }));
            appendAnswerChunk(answerChunk);
            return;
          }

          if (event === "references") {
            applyReferenceNumbers(data as number[]);
            return;
          }

          if (event === "completed") {
            const result = data as ChatRunResponse;
            handleTerminalEvent(result);
            setPendingReview(null);
            setReviewNote("");
            appendStreamLog("审核恢复后的工作流已完成");
            upsertStreamMessage(
              "assistant",
              result.answer || "当前没有生成答案。",
              result.citations,
            );
            resetStreamMessage();
            if (result.conversation_id) {
              setSelectedConversationId(result.conversation_id);
            }
            await loadConversationList(Number(selectedKnowledgeBaseId));
            return;
          }

          if (event === "error") {
            const errorEvent = data as ChatStreamErrorEvent;
            throw new ApiError(errorEvent.detail || "审核恢复失败", 500);
          }
        },
      );
    } catch (err) {
      removeStreamMessage();
      setError(err instanceof ApiError ? err.message : "审核恢复失败");
    } finally {
      setResumeAction(null);
    }
  }

  return (
    <Stack gap="md">
      {error ? (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="请求失败">
          {error}
        </Alert>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "280px minmax(520px, 1fr) 360px",
          gap: 0,
          height: "calc(100vh - 92px)",
          minHeight: 720,
          overflow: "hidden",
          border: "1px solid var(--mantine-color-gray-3)",
          borderRadius: 8,
          background: "#f6f8fb",
        }}
      >
        <aside
          style={{
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid var(--mantine-color-gray-3)",
            background: "white",
          }}
        >
          <Stack gap="sm" p="md" style={{ borderBottom: "1px solid var(--mantine-color-gray-3)" }}>
            <Text size="xs" fw={700} tt="uppercase" c="dimmed">
              知识库
            </Text>
            {initLoading ? (
              <Loader size="sm" />
            ) : (
              <Select
                data={knowledgeBases.map((knowledgeBase) => ({
                  value: String(knowledgeBase.id),
                  label: `${knowledgeBase.id} - ${knowledgeBase.name}`,
                }))}
                value={selectedKnowledgeBaseId}
                onChange={(value) => void handleKnowledgeBaseChange(value)}
                searchable
                leftSection={<IconMessageCircle size={15} />}
              />
            )}
            <Button
              fullWidth
              leftSection={<IconPlus size={16} />}
              onClick={handleStartNewConversation}
            >
              新建会话
            </Button>
          </Stack>

          <Group justify="space-between" px="md" pt="md" pb={8}>
            <Text size="xs" fw={700} tt="uppercase" c="dimmed">
              近期会话
            </Text>
            <Badge variant="light" size="sm">
              {conversations.length}
            </Badge>
          </Group>

          <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars>
            <Stack gap={6} px={8} pb="sm">
              {conversationLoading ? (
                <Loader size="sm" />
              ) : conversations.length === 0 ? (
                <Text size="sm" c="dimmed" px="sm">
                  当前知识库还没有会话记录。
                </Text>
              ) : (
                conversations.map((conversation) => {
                  const active = selectedConversationId === conversation.id;
                  return (
                    <Paper
                      key={conversation.id}
                      withBorder={active}
                      radius="sm"
                      p="sm"
                      style={{
                        cursor: "pointer",
                        borderColor: active ? "#91caff" : "transparent",
                        background: active ? "#eef7ff" : "white",
                        boxShadow: active ? "inset 3px 0 0 #228be6" : "none",
                      }}
                      onClick={() => void handleSelectConversation(conversation)}
                    >
                      <Stack gap={6}>
                        <Group justify="space-between" align="flex-start" wrap="nowrap">
                          <Group gap={6} style={{ minWidth: 0, flex: 1 }} wrap="nowrap">
                            {conversation.is_pinned ? (
                              <ThemeIcon size={18} variant="light" color="blue">
                                <IconPin size={12} />
                              </ThemeIcon>
                            ) : null}
                            <Text fw={650} size="sm" lineClamp={1} style={{ minWidth: 0 }}>
                              {conversation.title}
                            </Text>
                          </Group>
                          <Group gap={4} wrap="nowrap">
                            <Badge variant="light" size="xs">
                              {conversation.message_count}
                            </Badge>
                            <Menu position="bottom-end" withinPortal>
                              <Menu.Target>
                                <ActionIcon
                                  variant="subtle"
                                  color="gray"
                                  size="sm"
                                  onClick={(event) => event.stopPropagation()}
                                  loading={conversationActionLoading}
                                >
                                  <IconDotsVertical size={14} />
                                </ActionIcon>
                              </Menu.Target>
                              <Menu.Dropdown
                                onClick={(event) => event.stopPropagation()}
                              >
                                <Menu.Item
                                  leftSection={<IconPencil size={14} />}
                                  onClick={() => openRenameConversationModal(conversation)}
                                >
                                  重命名
                                </Menu.Item>
                                <Menu.Item
                                  leftSection={<IconPin size={14} />}
                                  onClick={() => void handleTogglePinConversation(conversation)}
                                >
                                  {conversation.is_pinned ? "取消置顶" : "置顶会话"}
                                </Menu.Item>
                                <Menu.Divider />
                                <Menu.Item
                                  color="red"
                                  leftSection={<IconTrash size={14} />}
                                  onClick={() => setDeleteConversationTarget(conversation)}
                                >
                                  删除
                                </Menu.Item>
                              </Menu.Dropdown>
                            </Menu>
                          </Group>
                        </Group>
                        <Text size="xs" c="dimmed" lineClamp={2}>
                          {conversation.last_message_preview || "暂无消息"}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {formatTime(conversation.updated_at)}
                        </Text>
                      </Stack>
                    </Paper>
                  );
                })
              )}
            </Stack>
          </ScrollArea>

          <Group p="sm" gap="xs" style={{ borderTop: "1px solid var(--mantine-color-gray-3)" }}>
            <IconHistory size={14} color="var(--mantine-color-gray-6)" />
            <Text size="xs" c="dimmed">
              线程 {shortThreadId(threadId)}
            </Text>
          </Group>
        </aside>

        <section
          style={{
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            background: "#f8fafc",
          }}
        >
          <Group
            h={70}
            px="xl"
            justify="space-between"
            style={{ borderBottom: "1px solid var(--mantine-color-gray-3)", background: "white" }}
          >
            <div>
              <Group gap="xs">
                <Text fw={750}>
                  {selectedConversation?.title || (threadId ? "当前问答线程" : "新建专家问答")}
                </Text>
                <Badge variant="light" color={getStatusColor(effectiveStatus, Boolean(pendingReview))}>
                  {effectiveStatus || "idle"}
                </Badge>
              </Group>
              <Text size="xs" c="dimmed" mt={4}>
                {selectedKnowledgeBase?.name ?? "未选择知识库"} · route {effectiveRoute}
              </Text>
            </div>
            <Tooltip label="清空当前本地会话并开启新线程">
              <ActionIcon variant="light" size="lg" onClick={handleStartNewConversation}>
                <IconRefresh size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>

          <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars>
            <Stack gap="lg" maw={860} mx="auto" px="xl" py="lg">
              {historyLoading ? (
                <Loader size="sm" />
              ) : messages.length === 0 && !pendingReview ? (
                <Paper withBorder radius="sm" p="xl" mt={72} ta="center">
                  <ThemeIcon color="blue" variant="light" size={44} mx="auto">
                    <IconRobot size={24} />
                  </ThemeIcon>
                  <Text fw={750} mt="md">
                    开始一轮专家问答
                  </Text>
                  <Text size="sm" c="dimmed" maw={420} mx="auto" mt="xs">
                    选择知识库后输入问题。Agent 会返回答案、引用来源，并在证据不足时把人工审核卡片插入对话流。
                  </Text>
                </Paper>
              ) : (
                <>
                  {messages.map((message) => (
                    <ChatMessageCard key={message.id} message={message} />
                  ))}
                  {pendingReview ? (
                    <ReviewInlineCard
                      pendingReview={pendingReview}
                      retrievedDocsPreviewItems={effectiveRetrievedDocsPreviewItems}
                      reviewNote={reviewNote}
                      setReviewNote={setReviewNote}
                      resumeAction={resumeAction}
                      onResume={(approved) => void handleResume(approved)}
                    />
                  ) : null}
                </>
              )}
            </Stack>
          </ScrollArea>

          <form
            onSubmit={handleSubmit}
            style={{
              borderTop: "1px solid var(--mantine-color-gray-3)",
              background: "white",
              padding: 16,
            }}
          >
            <Stack gap="xs" maw={860} mx="auto">
              {pendingReview ? (
                <Alert
                  color="yellow"
                  variant="light"
                  icon={<IconPlayerPause size={16} />}
                  radius="sm"
                >
                  当前问题等待人工审核，处理后才能继续提问。
                </Alert>
              ) : null}

              <Paper
                withBorder
                radius="sm"
                p="xs"
                style={{
                  background: pendingReview ? "#f8f9fa" : "white",
                  borderColor: pendingReview ? undefined : "#91caff",
                }}
              >
                <Group align="flex-end" wrap="nowrap" gap="sm">
                  <Textarea
                    value={question}
                    onChange={(event) => setQuestion(event.currentTarget.value)}
                    disabled={Boolean(pendingReview)}
                    placeholder="例如：采购复核的触发条件是什么？"
                    minRows={2}
                    autosize
                    style={{ flex: 1 }}
                  />
                  <NumberInput
                    label="Top K"
                    min={1}
                    max={10}
                    value={retrieveTopK}
                    onChange={(value) => setRetrieveTopK(Number(value) || 5)}
                    w={82}
                    disabled={Boolean(pendingReview)}
                  />
                  <Button
                    type="submit"
                    leftSection={<IconSend size={16} />}
                    loading={loading}
                    disabled={Boolean(pendingReview) || !selectedKnowledgeBaseId}
                  >
                    发送
                  </Button>
                </Group>
              </Paper>
            </Stack>
          </form>
        </section>

        <aside
          style={{
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            borderLeft: "1px solid var(--mantine-color-gray-3)",
            background: "white",
          }}
        >
          <Stack gap="sm" p="md" style={{ borderBottom: "1px solid var(--mantine-color-gray-3)" }}>
            <Group justify="space-between">
              <div>
                <Text fw={750}>运行与证据</Text>
                <Text size="xs" c="dimmed">
                  本轮问答的可审计上下文
                </Text>
              </div>
              <Badge variant="light" color={getStatusColor(effectiveStatus, Boolean(pendingReview))}>
                {effectiveStatus || "idle"}
              </Badge>
            </Group>
            <Tabs
              value={evidenceTab}
              onChange={(value) => value && setEvidenceTab(value as EvidenceTab)}
            >
              <Tabs.List grow>
                <Tabs.Tab value="process">运行过程</Tabs.Tab>
                <Tabs.Tab value="evidence">检索证据</Tabs.Tab>
                <Tabs.Tab value="citations">引用详情</Tabs.Tab>
              </Tabs.List>
            </Tabs>
          </Stack>

          <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars>
            <Stack gap="md" p="md">
              {evidenceTab === "process" ? (
                <>
                  <Timeline
                    active={effectiveStatus === "completed" ? 3 : pendingReview ? 2 : loading ? 1 : 0}
                    bulletSize={22}
                    lineWidth={1}
                  >
                    <Timeline.Item title="router" bullet={<IconChecks size={12} />}>
                      <Badge variant="light" color={effectiveRoute === "-" ? "gray" : "green"} size="xs">
                        {effectiveRoute}
                      </Badge>
                    </Timeline.Item>
                    <Timeline.Item title="retrieve" bullet={<IconChecks size={12} />}>
                      <Text size="xs" c="dimmed">
                        命中 {effectiveRetrievalHitCount} 条候选内容
                      </Text>
                    </Timeline.Item>
                    <Timeline.Item title="relevance_check" bullet={<IconPlayerPause size={12} />}>
                      <Badge
                        variant="light"
                        color={pendingReview ? "yellow" : effectiveRelevanceDecision === "-" ? "gray" : "teal"}
                        size="xs"
                      >
                        {effectiveRelevanceDecision}
                      </Badge>
                      {effectiveReviewReason !== "-" ? (
                        <Text size="xs" c="dimmed" mt={4}>
                          {effectiveReviewReason}
                        </Text>
                      ) : null}
                    </Timeline.Item>
                    <Timeline.Item title="answer" bullet={<IconRobot size={12} />}>
                      <Text size="xs" c="dimmed">
                        {effectiveStatus === "completed" ? "已完成" : "待执行"}
                      </Text>
                    </Timeline.Item>
                  </Timeline>

                  <Paper withBorder radius="sm" p="sm" bg="gray.0">
                    <Stack gap={6}>
                      <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                        路由原因
                      </Text>
                      <Text size="sm">{effectiveRouteReason}</Text>
                    </Stack>
                  </Paper>

                  <Paper withBorder radius="sm" p="sm">
                    <Stack gap="xs">
                      <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                        Node trace
                      </Text>
                      {effectiveNodeTrace.length ? (
                        <Group gap={6}>
                          {effectiveNodeTrace.map((node) => (
                            <Badge key={node} variant="outline" size="sm">
                              {node}
                            </Badge>
                          ))}
                        </Group>
                      ) : (
                        <Text size="xs" c="dimmed">
                          暂无节点轨迹
                        </Text>
                      )}
                    </Stack>
                  </Paper>

                  {streamLogs.length ? (
                    <Paper withBorder radius="sm" p="sm">
                      <Stack gap={6}>
                        <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                          实时进度
                        </Text>
                        {streamLogs.map((log, index) => (
                          <Text key={`${log}-${index}`} size="xs" c="dimmed">
                            {log}
                          </Text>
                        ))}
                      </Stack>
                    </Paper>
                  ) : null}
                </>
              ) : null}

              {evidenceTab === "evidence" ? (
                <Stack gap="sm">
                  <Group justify="space-between">
                    <Text size="sm" fw={700}>
                      候选证据 Top-K
                    </Text>
                  <Badge variant="light" color="blue">
                    top {effectiveRetrievalHitCount}
                  </Badge>
                </Group>
                  {effectiveRetrievedDocsPreviewItems.length ? (
                    effectiveRetrievedDocsPreviewItems.map((item) => (
                      <Paper key={`${item.chunk_id}-${item.index}`} withBorder radius="sm" p="sm">
                        <Stack gap={6}>
                          <Group justify="space-between" wrap="nowrap" gap="xs">
                            <Text size="sm" fw={650} lineClamp={1}>
                              [{item.index}] {item.title || "未命名来源"}
                            </Text>
                            <Badge variant="light" color="blue" size="xs">
                              score {formatScore(item.score)}
                            </Badge>
                          </Group>
                          <Text size="xs" c="dimmed">
                            doc_id={item.doc_id ?? "-"} | chunk_id={item.chunk_id ?? "-"}
                          </Text>
                          <Text size="xs" style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
                            {item.content || item.content_preview || "暂无内容"}
                          </Text>
                        </Stack>
                      </Paper>
                    ))
                  ) : docsPreviewLines.length ? (
                    docsPreviewLines.map((line, index) => (
                      <Paper key={`${line}-${index}`} withBorder radius="sm" p="sm">
                        <Text size="xs" style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                          {line}
                        </Text>
                      </Paper>
                    ))
                  ) : (
                    <Text size="sm" c="dimmed">
                      暂无检索证据。发送问题后这里会展示 docs preview。
                    </Text>
                  )}
                </Stack>
              ) : null}

              {evidenceTab === "citations" ? (
                <Stack gap="sm">
                  <Group justify="space-between">
                    <Text size="sm" fw={700}>
                      引用详情
                    </Text>
                    <Badge variant="light">{latestCitations.length}</Badge>
                  </Group>
                  {latestCitations.length ? (
                    latestCitations.map((citation, index) => (
                      <Paper key={`${citation.chunk_id}-${index}`} withBorder radius="sm" p="sm">
                        <Stack gap={4}>
                          <Text size="sm" fw={650}>
                            {citation.title}
                          </Text>
                          <Text size="xs" c="dimmed">
                            doc_id={citation.doc_id} | chunk_id={citation.chunk_id}
                          </Text>
                          <Badge variant="light" color="blue" size="xs">
                            score {formatScore(citation.score)}
                          </Badge>
                        </Stack>
                      </Paper>
                    ))
                  ) : (
                    <Text size="sm" c="dimmed">
                      当前还没有最终引用。
                    </Text>
                  )}
                </Stack>
              ) : null}
            </Stack>
          </ScrollArea>
        </aside>
      </div>

      <Modal
        opened={Boolean(renameConversationTarget)}
        onClose={() => {
          setRenameConversationTarget(null);
          setRenameConversationTitle("");
        }}
        title="重命名会话"
        centered
      >
        <Stack gap="md">
          <TextInput
            label="会话标题"
            value={renameConversationTitle}
            onChange={(event) =>
              setRenameConversationTitle(event.currentTarget.value)
            }
            maxLength={200}
            autoFocus
          />
          <Group justify="flex-end">
            <Button
              variant="subtle"
              onClick={() => {
                setRenameConversationTarget(null);
                setRenameConversationTitle("");
              }}
            >
              取消
            </Button>
            <Button
              loading={conversationActionLoading}
              onClick={() => void handleRenameConversation()}
            >
              保存
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={Boolean(deleteConversationTarget)}
        onClose={() => setDeleteConversationTarget(null)}
        title="删除会话"
        centered
      >
        <Stack gap="md">
          <Text size="sm">
            删除后会移除这个会话下的历史消息和待审核任务。当前第一版不做回收站。
          </Text>
          <Paper withBorder radius="sm" p="sm" bg="gray.0">
            <Text size="sm" fw={650}>
              {deleteConversationTarget?.title || "未命名会话"}
            </Text>
          </Paper>
          <Group justify="flex-end">
            <Button
              variant="subtle"
              onClick={() => setDeleteConversationTarget(null)}
            >
              取消
            </Button>
            <Button
              color="red"
              loading={conversationActionLoading}
              onClick={() => void handleDeleteConversation()}
            >
              删除
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
