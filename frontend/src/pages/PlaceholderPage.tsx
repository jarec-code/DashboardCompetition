import { Paper, Stack, Text, Title } from "@mantine/core";

import type { NavItem } from "../layout/navigation";

/** Shown for sections that are not implemented yet: no fake data. */
export function PlaceholderPage({ item }: { item: NavItem }) {
  return (
    <Stack>
      <Title order={1} size="h2">{item.label}</Title>
      <Paper withBorder p="xl" radius="md">
        <Text fw={500}>Táto sekcia sa pripravuje.</Text>
        <Text c="dimmed" size="sm" mt={4}>
          Bude dostupná po dokončení etapy {item.stage} implementačného plánu.
        </Text>
      </Paper>
    </Stack>
  );
}
