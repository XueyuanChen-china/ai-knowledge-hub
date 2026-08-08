import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";

import LoginPage from "@/app/login/page";
import { AppFrame } from "@/components/app-frame";
import { AuthGate } from "@/components/auth-gate";
import { PageHeader } from "@/components/page-header";
import { Card, Stack, Text } from "@mantine/core";

const HomePage = lazy(() => import("@/app/page"));
const AccountPage = lazy(() => import("@/app/account/page"));
const UserManagementPage = lazy(() => import("@/app/admin/users/page"));
const ChatPage = lazy(() => import("@/app/chat/page"));
const DocumentsPage = lazy(() => import("@/app/documents/page"));
const KnowledgeBaseDetailPage = lazy(() => import("@/app/knowledge-bases/[id]/page"));
const KnowledgeBasesPage = lazy(() => import("@/app/knowledge-bases/page"));
const KnowledgeItemDetailPage = lazy(() => import("@/app/knowledge-items/[id]/page"));
const SearchPage = lazy(() => import("@/app/search/page"));

function RouteLoading() {
  return <Text c="dimmed">正在加载页面...</Text>;
}

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
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <AuthGate>
              <Suspense fallback={<RouteLoading />}>
                <ShellLayout />
              </Suspense>
            </AuthGate>
          }
        >
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
          <Route path="/account" element={<AccountPage />} />
          <Route path="/admin/users" element={<UserManagementPage />} />
          <Route path="/index.html" element={<Navigate to="/" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
