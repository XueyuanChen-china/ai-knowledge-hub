import { Group, Stack, Text, Title } from "@mantine/core";

export function PageHeader({
  title,
  description,
  rightSection,
}: {
  title: string;
  description: string;
  rightSection?: React.ReactNode;
}) {
  return (
    <Group justify="space-between" align="flex-start" mb="lg">
      <Stack gap={4}>
        <Title order={2}>{title}</Title>
        <Text c="dimmed" size="sm">
          {description}
        </Text>
      </Stack>
      {rightSection}
    </Group>
  );
}
