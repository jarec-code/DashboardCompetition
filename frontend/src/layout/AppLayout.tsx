import { AppShell, Badge, Burger, Button, Group, NavLink, Stack, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconLogout } from "@tabler/icons-react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { useMonitoring } from "../api/monitoring";
import { useLogout, useSession } from "../auth/auth";
import { NAV_ITEMS } from "./navigation";

const ROLE_LABELS = { admin: "administrátor", viewer: "čitateľ" } as const;

export function AppLayout() {
  const [opened, { toggle, close }] = useDisclosure();
  const { data: session } = useSession();
  const logout = useLogout();
  const location = useLocation();
  const monitoring = useMonitoring(60);
  const problems = (monitoring.data?.problem_competitors?.length ?? 0) + (monitoring.data?.problem_offers?.length ?? 0);

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 240, breakpoint: "sm", collapsed: { mobile: !opened } }}
              padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <Text fw={700}>Sledovanie cien</Text>
          </Group>
          {session && (
            <Group gap="sm" wrap="nowrap">
              <Text size="sm" c="dimmed" visibleFrom="xs" truncate maw={220}>{session.user.email}</Text>
              <Badge variant="light" visibleFrom="xs">{ROLE_LABELS[session.user.role]}</Badge>
              <Button variant="subtle" size="compact-sm" leftSection={<IconLogout size={16} />}
                      loading={logout.isPending} onClick={() => logout.mutate()}>
                Odhlásiť
              </Button>
            </Group>
          )}
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Stack gap={2}>
          {NAV_ITEMS.map((item) => {
            const active = item.path === "/" ? location.pathname === "/" : location.pathname.startsWith(item.path);
            return (
              <NavLink key={item.path} component={Link} to={item.path} label={item.label} active={active}
                       leftSection={<item.icon size={18} stroke={1.6} />} onClick={close}
                       rightSection={item.path === "/aktualizacie" && problems > 0
                         ? <Badge size="xs" color="red" circle aria-label={`${problems} problémov`}>{problems}</Badge>
                         : undefined} />
            );
          })}
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
