import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import HomePage from "@/app/page";
import ChatPage from "@/app/chat/page";
import DocumentsPage from "@/app/documents/page";
import KnowledgeBaseDetailPage from "@/app/knowledge-bases/[id]/page";
import KnowledgeBasesPage from "@/app/knowledge-bases/page";
import KnowledgeItemDetailPage from "@/app/knowledge-items/[id]/page";
import SearchPage from "@/app/search/page";
import { AppFrame } from "@/components/app-frame";
import { PageHeader } from "@/components/page-header";
import { Card, Stack, Text } from "@mantine/core";

function ShellLayout() {
  return (
    <AppFrame>
      <Outlet />
    </AppFrame>
  );
}

function NotFoundPage() {
  return (
    <Stack gap="lg">
      <PageHeader title="页面不存在" description="当前路由没有对应页面。" />
      <Card withBorder radius="sm" padding="lg">
        <Text size="sm" c="dimmed">
          请从左侧导航返回有效页面。
        </Text>
      </Card>
    </Stack>
  );
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<ShellLayout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/knowledge-bases" element={<KnowledgeBasesPage />} />
          <Route
            path="/knowledge-bases/:id"
            element={<KnowledgeBaseDetailPage />}
          />
          <Route
            path="/knowledge-items/:id"
            element={<KnowledgeItemDetailPage />}
          />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/index.html" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
