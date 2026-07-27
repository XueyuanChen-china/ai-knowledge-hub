import type {
  ChatRunResponse,
  ChatStreamErrorEvent,
  ChatStreamEventName,
  ChatStreamNodeEvent,
  ChatStreamStartEvent,
  AuthMeResponse,
  AuthTokenResponse,
  ChunkRecord,
  ConversationMessage,
  ConversationSummary,
  DashboardSummary,
  DocumentIndexResponse,
  DocumentRecord,
  KnowledgeBase,
  KnowledgeBasePayload,
  KnowledgeItem,
  KnowledgeItemChunkResponse,
  KnowledgeItemIndexResponse,
  KnowledgeItemPayload,
  MemberCreatePayload,
  OrganizationMember,
  OrganizationRole,
  SecurityAuditLogListResponse,
  SemanticSearchResult,
} from "@/lib/api/types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ??
  "http://127.0.0.1:8000";

const ACCESS_TOKEN_STORAGE_KEY = "ai-knowledge-hub.access-token";
const AUTH_EXPIRED_EVENT = "ai-knowledge-hub.auth-expired";

// 统一的前端请求错误类型，后面页面里可以直接拿到状态码和提示文案。
class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getAuthToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
}

export function setAuthToken(token: string): void {
  sessionStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token);
}

export function clearAuthSession(): void {
  sessionStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
}

function notifyAuthExpired(): void {
  clearAuthSession();
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 用 Headers 包一下，方便后面按需补充 Content-Type 等请求头。
  const headers = new Headers(init?.headers);
  const token = getAuthToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // FormData 让浏览器自己带 multipart boundary，只有普通 JSON body 才手动补头。
  if (
    init?.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401) {
      notifyAuthExpired();
    }
    // 后端大多数错误会返回 {"detail": "..."}，这里优先把 detail 提出来给页面展示。
    let detail = response.statusText;
    try {
      const data = (await response.json()) as { detail?: string };
      detail = data.detail ?? detail;
    } catch {
      // 忽略解析失败，保留原始 statusText。
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    // DELETE 这种无响应体场景，直接返回 undefined。
    return undefined as T;
  }

  const responseText = await response.text();
  if (!responseText.trim()) {
    // 有些服务或代理会用 200 返回空 body，这里也按无响应体处理。
    return undefined as T;
  }

  return JSON.parse(responseText) as T;
}

async function readErrorDetail(response: Response): Promise<string> {
  let detail = response.statusText;
  try {
    const data = (await response.json()) as { detail?: string };
    detail = data.detail ?? detail;
  } catch {
    // SSE 或纯文本错误体不一定能按 JSON 解析，保留默认状态文案即可。
  }
  return detail;
}

type ChatStreamEventData =
  | ChatStreamStartEvent
  | ChatStreamNodeEvent
  | string
  | number[]
  | ChatRunResponse
  | ChatStreamErrorEvent;

function parseSseEventChunk(chunk: string): {
  event: ChatStreamEventName;
  data: ChatStreamEventData;
} | null {
  const lines = chunk
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean);

  if (lines.length === 0) {
    return null;
  }

  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }

  if (!dataLines.length) {
    return null;
  }

  const rawData = dataLines
    .map((line) => (line.startsWith(" ") ? line.slice(1) : line))
    .join("\n");

  try {
    if (eventName === "answer") {
      return {
        event: eventName as ChatStreamEventName,
        data: rawData,
      };
    }

    if (eventName === "references") {
      return {
        event: eventName as ChatStreamEventName,
        data: JSON.parse(rawData) as number[],
      };
    }

    return {
      event: eventName as ChatStreamEventName,
      data: JSON.parse(rawData) as ChatStreamEventData,
    };
  } catch {
    return null;
  }
}

async function streamRequest(
  path: string,
  payload: unknown,
  onEvent: (
    event: ChatStreamEventName,
    data: ChatStreamEventData,
  ) => void | Promise<void>,
): Promise<void> {
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  });
  const token = getAuthToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401) {
      notifyAuthExpired();
    }
    throw new ApiError(await readErrorDetail(response), response.status);
  }

  if (!response.body) {
    throw new ApiError("浏览器没有拿到可读流", 500);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), {
      stream: !done,
    });

    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const parsed = parseSseEventChunk(chunk);
      if (!parsed) {
        continue;
      }
      await onEvent(parsed.event, parsed.data);
    }

    if (done) {
      break;
    }
  }

  if (buffer.trim()) {
    const parsed = parseSseEventChunk(buffer);
    if (parsed) {
      await onEvent(parsed.event, parsed.data);
    }
  }
}

export async function login(
  email: string,
  password: string,
): Promise<AuthTokenResponse> {
  // 登录请求故意不依赖已有 token，成功后才写入当前标签页 sessionStorage。
  return request<AuthTokenResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function getCurrentUser(): Promise<AuthMeResponse> {
  return request<AuthMeResponse>("/api/auth/me");
}

export async function logout(): Promise<void> {
  try {
    await request<void>("/api/auth/logout", { method: "POST" });
  } finally {
    // 即使 Redis 暂时不可用，也清理本地 token，避免当前页面继续带着旧凭证请求。
    clearAuthSession();
  }
}

export async function getOrganizationMembers(): Promise<OrganizationMember[]> {
  return request<OrganizationMember[]>("/api/admin/users");
}

export async function createOrganizationMember(
  payload: MemberCreatePayload,
): Promise<OrganizationMember> {
  return request<OrganizationMember>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateOrganizationMemberRole(
  userId: number,
  role: OrganizationRole,
): Promise<OrganizationMember> {
  return request<OrganizationMember>(`/api/admin/users/${userId}/role`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

export async function updateOrganizationMemberStatus(
  userId: number,
  isActive: boolean,
): Promise<OrganizationMember> {
  return request<OrganizationMember>(`/api/admin/users/${userId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

export async function resetOrganizationMemberPassword(
  userId: number,
  newPassword: string,
): Promise<void> {
  return request<void>(`/api/admin/users/${userId}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ new_password: newPassword }),
  });
}

export async function removeOrganizationMember(userId: number): Promise<void> {
  return request<void>(`/api/admin/users/${userId}`, {
    method: "DELETE",
  });
}

export async function getSecurityAuditLogs(
  offset = 0,
  limit = 50,
): Promise<SecurityAuditLogListResponse> {
  return request<SecurityAuditLogListResponse>(
    `/api/admin/audit-logs?offset=${offset}&limit=${limit}`,
  );
}

export async function changeCurrentPassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await request<void>("/api/account/change-password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  // 修改密码会让服务端递增 token_version，当前 token 已不再有效。
  clearAuthSession();
}

export async function logoutAllDevices(): Promise<void> {
  await request<void>("/api/account/logout-all", { method: "POST" });
  clearAuthSession();
}

export async function getKnowledgeBases(): Promise<KnowledgeBase[]> {
  return request<KnowledgeBase[]>("/knowledge-bases");
}

export async function getKnowledgeBase(
  knowledgeBaseId: number,
): Promise<KnowledgeBase> {
  return request<KnowledgeBase>(`/knowledge-bases/${knowledgeBaseId}`);
}

export async function createKnowledgeBase(
  payload: KnowledgeBasePayload,
): Promise<KnowledgeBase> {
  return request<KnowledgeBase>("/knowledge-bases", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateKnowledgeBase(
  knowledgeBaseId: number,
  payload: KnowledgeBasePayload,
): Promise<KnowledgeBase> {
  return request<KnowledgeBase>(`/knowledge-bases/${knowledgeBaseId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteKnowledgeBase(
  knowledgeBaseId: number,
): Promise<void> {
  return request<void>(`/knowledge-bases/${knowledgeBaseId}`, {
    method: "DELETE",
  });
}

export async function getKnowledgeItems(
  knowledgeBaseId?: number,
): Promise<KnowledgeItem[]> {
  const query = knowledgeBaseId
    ? `?knowledge_base_id=${knowledgeBaseId}`
    : "";
  return request<KnowledgeItem[]>(`/knowledge-items${query}`);
}

export async function getKnowledgeItem(
  knowledgeItemId: number,
): Promise<KnowledgeItem> {
  return request<KnowledgeItem>(`/knowledge-items/${knowledgeItemId}`);
}

export async function createKnowledgeItem(
  payload: KnowledgeItemPayload,
): Promise<KnowledgeItem> {
  return request<KnowledgeItem>("/knowledge-items", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateKnowledgeItem(
  knowledgeItemId: number,
  payload: KnowledgeItemPayload,
): Promise<KnowledgeItem> {
  return request<KnowledgeItem>(`/knowledge-items/${knowledgeItemId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteKnowledgeItem(
  knowledgeItemId: number,
): Promise<void> {
  return request<void>(`/knowledge-items/${knowledgeItemId}`, {
    method: "DELETE",
  });
}

export async function getKnowledgeItemChunks(
  knowledgeItemId: number,
): Promise<ChunkRecord[]> {
  return request<ChunkRecord[]>(`/knowledge-items/${knowledgeItemId}/chunks`);
}

export async function splitKnowledgeItemIntoChunks(
  knowledgeItemId: number,
): Promise<KnowledgeItemChunkResponse> {
  return request<KnowledgeItemChunkResponse>(
    `/knowledge-items/${knowledgeItemId}/chunks`,
    {
      method: "POST",
    },
  );
}

export async function indexKnowledgeItem(
  knowledgeItemId: number,
): Promise<KnowledgeItemIndexResponse> {
  return request<KnowledgeItemIndexResponse>(
    `/knowledge-items/${knowledgeItemId}/index`,
    {
      method: "POST",
    },
  );
}

export async function getDocuments(
  knowledgeBaseId?: number,
): Promise<DocumentRecord[]> {
  const query = knowledgeBaseId
    ? `?knowledge_base_id=${knowledgeBaseId}`
    : "";
  return request<DocumentRecord[]>(`/documents${query}`);
}

export async function uploadDocument(payload: {
  knowledgeBaseId: number;
  file: File;
}): Promise<DocumentRecord> {
  const formData = new FormData();
  formData.append("knowledge_base_id", String(payload.knowledgeBaseId));
  formData.append("file", payload.file);

  return request<DocumentRecord>("/documents", {
    method: "POST",
    body: formData,
  });
}

export async function indexDocument(
  documentId: number,
): Promise<DocumentIndexResponse> {
  return request<DocumentIndexResponse>(`/documents/${documentId}/index`, {
    method: "POST",
  });
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  // 首页总览需要的两份数据并行拉，避免串行请求拖慢首屏。
  const [knowledgeBases, knowledgeItems] = await Promise.all([
    getKnowledgeBases(),
    getKnowledgeItems(),
  ]);

  return { knowledgeBases, knowledgeItems };
}

export async function runChat(payload: {
  knowledge_base_id: number;
  question: string;
  thread_id?: string;
  retrieve_top_k?: number;
}): Promise<ChatRunResponse> {
  return request<ChatRunResponse>("/api/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function streamChat(
  payload: {
    knowledge_base_id: number;
    question: string;
    thread_id?: string;
    retrieve_top_k?: number;
  },
  onEvent: (
    event: ChatStreamEventName,
    data: ChatStreamEventData,
  ) => void | Promise<void>,
): Promise<void> {
  return streamRequest("/api/chat/stream", payload, onEvent);
}

export async function resumeChat(payload: {
  thread_id: string;
  approved: boolean;
  human_note?: string;
  retrieve_top_k?: number;
}): Promise<ChatRunResponse> {
  return request<ChatRunResponse>("/api/review/resume", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function streamResumeChat(
  payload: {
    thread_id: string;
    approved: boolean;
    human_note?: string;
    retrieve_top_k?: number;
  },
  onEvent: (
    event: ChatStreamEventName,
    data: ChatStreamEventData,
  ) => void | Promise<void>,
): Promise<void> {
  return streamRequest("/api/review/resume/stream", payload, onEvent);
}

export async function getConversations(
  knowledgeBaseId?: number,
): Promise<ConversationSummary[]> {
  const query = knowledgeBaseId
    ? `?knowledge_base_id=${knowledgeBaseId}`
    : "";
  return request<ConversationSummary[]>(`/api/conversations${query}`);
}

export async function getConversationMessages(
  conversationId: number,
): Promise<ConversationMessage[]> {
  return request<ConversationMessage[]>(
    `/api/conversations/${conversationId}/messages`,
  );
}

export async function updateConversation(
  conversationId: number,
  payload: {
    title?: string;
    is_pinned?: boolean;
  },
): Promise<ConversationSummary> {
  return request<ConversationSummary>(`/api/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteConversation(conversationId: number): Promise<void> {
  return request<void>(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
}

export async function searchSemantic(payload: {
  knowledge_base_id: number;
  query: string;
  top_k?: number;
}): Promise<SemanticSearchResult[]> {
  return request<SemanticSearchResult[]>("/search/semantic", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export { API_BASE_URL, ApiError };
