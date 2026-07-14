"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AppShell,
  Badge,
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
} from "@tabler/icons-react";

const NAV_ITEMS = [
  { href: "/", label: "总览", icon: IconLayoutDashboard },
  { href: "/knowledge-bases", label: "知识库", icon: IconDatabase },
  { href: "/documents", label: "文档上传", icon: IconFileUpload },
  { href: "/search", label: "语义搜索", icon: IconDatabaseSearch },
  { href: "/chat", label: "专家问答", icon: IconMessageCircle },
];

export function AppFrame({ children }: { children: React.ReactNode }) {
  // opened 只控制移动端侧边栏是否展开，桌面端默认始终可见。
  const [opened, { toggle }] = useDisclosure();
  // pathname 用来给当前路由高亮对应导航项。
  const pathname = usePathname();

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
          <Badge variant="light" color="blue">
            Week 4
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Stack gap="xs">
          {NAV_ITEMS.map((item) => (
            // 这里复用 Mantine 的 NavLink 做侧边导航，高亮规则直接对比当前 pathname。
            <NavLink
              key={item.href}
              component={Link}
              href={item.href}
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
