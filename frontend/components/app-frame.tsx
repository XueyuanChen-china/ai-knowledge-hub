import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  AppShell,
  Badge,
  Button,
  Burger,
  Group,
  NavLink,
  Stack,
  Text,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconBrain,
  IconDatabase,
  IconDatabaseSearch,
  IconFileUpload,
  IconLayoutDashboard,
  IconMessageCircle,
  IconLogout,
  IconUserCircle,
  IconUsers,
} from "@tabler/icons-react";
import { useAuth } from "@/components/auth-context-value";
import { logout } from "@/lib/api/client";

const NAV_ITEMS = [
  { href: "/", label: "总览", icon: IconLayoutDashboard },
  { href: "/knowledge-bases", label: "知识库", icon: IconDatabase },
  { href: "/documents", label: "文档上传", icon: IconFileUpload },
  { href: "/search", label: "语义搜索", icon: IconDatabaseSearch },
  { href: "/chat", label: "专家问答", icon: IconMessageCircle },
];

const ACCOUNT_NAV_ITEM = {
  href: "/account",
  label: "我的账号",
  icon: IconUserCircle,
};

const ADMIN_NAV_ITEM = {
  href: "/admin/users",
  label: "成员管理",
  icon: IconUsers,
};

export function AppFrame({ children }: { children: React.ReactNode }) {
  // opened 只控制移动端侧边栏是否展开，桌面端默认始终可见。
  const [opened, { toggle }] = useDisclosure();
  const pathname = useLocation().pathname;
  const navigate = useNavigate();
  const principal = useAuth();
  const canManageUsers = ["owner", "admin"].includes(principal.role);
  const navItems = [
    ...NAV_ITEMS,
    ...(canManageUsers ? [ADMIN_NAV_ITEM] : []),
    ACCOUNT_NAV_ITEM,
  ];

  function isActivePath(href: string) {
    if (href === "/") {
      return pathname === "/";
    }
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 248, breakpoint: "sm", collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header px="md">
        <Group h="100%" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Group gap={10}>
              <IconBrain size={20} />
              <div>
                <Text fw={700} size="sm">
                  AI Knowledge Hub
                </Text>
                <Text c="dimmed" size="xs">
                  Frontend Workspace
                </Text>
              </div>
            </Group>
          </Group>
          <Group gap="sm">
            <div>
              <Text size="xs" fw={600} ta="right">
                {principal.user.email}
              </Text>
              <Text size="xs" c="dimmed" ta="right">
                {principal.organization.name} · {principal.role}
              </Text>
            </div>
            <Badge variant="light" color="blue">{principal.role}</Badge>
            <Button
              variant="subtle"
              color="gray"
              size="compact-sm"
              leftSection={<IconLogout size={15} />}
              onClick={async () => {
                try {
                  await logout();
                } finally {
                  // 服务端撤销失败时也离开页面，避免用户卡在已退出的界面。
                  navigate("/login", { replace: true });
                }
              }}
            >
              退出登录
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Stack gap="xs">
          {navItems.map((item) => (
            // 这里复用 Mantine 的 NavLink 做侧边导航，高亮规则直接对比当前 pathname。
            <NavLink
              key={item.href}
              component={Link}
              to={item.href}
              active={isActivePath(item.href)}
              label={item.label}
              leftSection={<item.icon size={18} stroke={1.6} />}
            />
          ))}
        </Stack>
      </AppShell.Navbar>

      {/* Main 区域就是每个具体页面真正渲染内容的位置。 */}
      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
