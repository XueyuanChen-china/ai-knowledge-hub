export interface KnowledgeBase {
  id: number;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface AuthUser {
  id: number;
  email: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface AuthOrganization {
  id: number;
  name: string;
  slug: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
  organization: AuthOrganization;
  role: string;
}

export interface AuthMeResponse {
  user: AuthUser;
  organization: AuthOrganization;
  role: string;
}

export type OrganizationRole = "owner" | "admin" | "editor" | "viewer";

export interface OrganizationMember {
  membership_id: number;
  role: OrganizationRole;
  joined_at: string;
  user: AuthUser;
}

export interface MemberCreatePayload {
  email: string;
  initial_password: string;
  role: OrganizationRole;
}

export interface SecurityAuditLog {
  id: number;
  organization_id: number | null;
  actor_user_id: number | null;
  actor_email: string;
  action: string;
  outcome: string;
  target_type: string;
  target_id: string;
  ip_address: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface SecurityAuditLogListResponse {
  items: SecurityAuditLog[];
  total: number;
  offset: number;
  limit: number;
}

export interface KnowledgeBasePayload {
  name: string;
  description: string;
}

export interface KnowledgeItem {
  id: number;
  knowledge_base_id: number;
  title: string;
  content: string;
  tags: string;
  status: "draft" | "active" | "disabled";
  source_type: string;
  source_document_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeItemPayload {
  knowledge_base_id: number;
  title: string;
  content: string;
  tags: string;
  status: "draft" | "active" | "disabled";
}

export interface KnowledgeItemChunkResponse {
  knowledge_item_id: number;
  chunk_count: number;
}

export interface KnowledgeItemIndexResponse {
  knowledge_item_id: number;
  chunk_count: number;
  vector_count: number;
  index_name: string;
}

export interface DocumentRecord {
  id: number;
  knowledge_base_id: number;
  filename: string;
  file_path: string;
  file_type: string;
  status: "uploaded" | "indexed" | "failed";
  extracted_text: string;
  created_at: string;
}

export interface DocumentIndexResponse {
  document_id: number;
  knowledge_item_id: number;
  chunk_count: number;
  vector_count: number;
  index_name: string;
}

export interface ChunkRecord {
  id: number;
  knowledge_base_id: number;
  document_id: number | null;
  knowledge_item_id: number;
  chunk_index: number;
  content: string;
  vector_id: string | null;
  metadata_json: string;
  created_at: string;
}

export interface SemanticSearchResult {
  doc_id: number | null;
  chunk_id: number | null;
  title: string;
  content_preview: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface ChatCitation {
  doc_id: number;
  chunk_id: number;
  knowledge_item_id: number;
  title: string;
  score: number;
}

export interface RetrievedDocPreviewItem {
  index: number;
  doc_id: number | null;
  chunk_id: number | null;
  knowledge_item_id: number | null;
  title: string;
  content: string;
  content_preview: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface ConversationSummary {
  id: number;
  knowledge_base_id: number;
  title: string;
  thread_id: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string;
  last_message_role: string;
}

export interface ConversationMessage {
  id: number;
  conversation_id: number;
  role: string;
  content: string;
  citations: ChatCitation[];
  created_at: string;
}

export interface ChatReviewPayload {
  question: string;
  thread_id: string;
  route: string;
  docs_preview: string;
  retrieval_hit_count: number;
  relevance_score: number;
  review_reason: string;
  citations: ChatCitation[];
}

export interface ChatRunResponse {
  status: string;
  thread_id: string;
  conversation_id: number | null;
  route: string;
  route_reason: string;
  answer: string;
  citations: ChatCitation[];
  need_human_review: boolean;
  review_reason: string;
  review_payload: ChatReviewPayload | null;
  docs_preview: string;
  retrieved_docs_preview_items: RetrievedDocPreviewItem[];
  relevance_decision: string;
  retrieval_hit_count: number;
  answer_used_fallback: boolean | null;
  node_trace: string[];
}

export type ChatStreamEventName =
  | "start"
  | "node"
  | "answer"
  | "references"
  | "interrupted"
  | "completed"
  | "error";

export interface ChatStreamStartEvent {
  thread_id: string;
  conversation_id: number | null;
  knowledge_base_id?: number;
  question?: string;
  approved?: boolean;
}

export interface ChatStreamNodeEvent {
  node: string;
  node_trace: string[];
  route?: string;
  route_reason?: string;
  retrieval_hit_count?: number;
  docs_preview?: string;
  retrieved_docs_preview_items?: RetrievedDocPreviewItem[];
  relevance_decision?: string;
  review_reason?: string;
  need_human_review?: boolean;
  human_note?: string;
  answer?: string;
  citations?: ChatCitation[];
  answer_used_fallback?: boolean | null;
}

export interface ChatStreamErrorEvent {
  detail: string;
}

export interface DashboardSummary {
  knowledgeBases: KnowledgeBase[];
  knowledgeItems: KnowledgeItem[];
}
