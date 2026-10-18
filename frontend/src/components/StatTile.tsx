import { Group, Paper, Text } from "@mantine/core";
import type { ReactNode } from "react";

/** KPI tile: sentence-case label, semibold value, optional context line. Text keeps text colors;
 *  a status is shown by an icon next to the value, never by coloring the number alone. */
export function StatTile({ label, value, hint, icon }: {
  label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode;
}) {
  return (
    <Paper withBorder p="md" radius="md">
      <Text size="sm" c="dimmed">{label}</Text>
      <Group gap={8} align="center" wrap="nowrap">
        <Text fz={28} fw={600} lh={1.3}>{value}</Text>
        {icon}
      </Group>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </Paper>
  );
}
