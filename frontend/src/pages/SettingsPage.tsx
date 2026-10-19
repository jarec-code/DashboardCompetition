import {
  Alert, Badge, Button, Group, Modal, Paper, PasswordInput, Select, Stack, Switch, Table, Text, TextInput, Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { Role, User } from "../api/types";
import { useSession } from "../auth/auth";
import { AlertRulesSection } from "./AlertRulesSection";
import { CatalogSyncSection } from "./CatalogSyncSection";
import { PasswordSection } from "./PasswordSection";
import { SchedulingSettingsSection } from "./SchedulingSettings";

const USERS_KEY = ["users"] as const;
const ROLE_OPTIONS = [
  { value: "viewer", label: "Čitateľ (iba prezeranie)" },
  { value: "admin", label: "Administrátor" },
];

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("sk-SK") : "—";
}

function CreateUserModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const create = useMutation({
    mutationFn: () => api<User>("/users", { method: "POST", body: { email, password, role } }),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: USERS_KEY });
      notifications.show({ color: "green", message: `Používateľ ${user.email} bol vytvorený.` });
      setEmail("");
      setPassword("");
      onClose();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Nový používateľ">
      <form onSubmit={submit}>
        <Stack>
          {create.error && <Alert color="red">{create.error.message}</Alert>}
          <TextInput label="E-mail" type="email" required value={email}
                     onChange={(e) => setEmail(e.currentTarget.value)} />
          <PasswordInput label="Heslo" description="Aspoň 10 znakov" required minLength={10} value={password}
                         onChange={(e) => setPassword(e.currentTarget.value)} />
          <Select label="Rola" data={ROLE_OPTIONS} value={role} allowDeselect={false}
                  onChange={(value) => setRole((value as Role) ?? "viewer")} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Zrušiť</Button>
            <Button type="submit" loading={create.isPending}>Vytvoriť</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

function UsersSection({ currentUserId }: { currentUserId: number }) {
  const queryClient = useQueryClient();
  const [opened, { open, close }] = useDisclosure();
  const users = useQuery({ queryKey: USERS_KEY, queryFn: () => api<User[]>("/users") });
  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: number; role?: Role; is_active?: boolean }) =>
      api<User>(`/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
    onError: (error) => notifications.show({ color: "red", message: error.message }),
  });

  return (
    <Paper withBorder p="md" radius="md">
      <Group justify="space-between" mb="sm">
        <Title order={2} size="h4">Používatelia</Title>
        <Button leftSection={<IconPlus size={16} />} size="sm" onClick={open}>Pridať používateľa</Button>
      </Group>
      {users.error && <Alert color="red">{users.error.message}</Alert>}
      <Table.ScrollContainer minWidth={560}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>E-mail</Table.Th>
              <Table.Th>Rola</Table.Th>
              <Table.Th>Aktívny</Table.Th>
              <Table.Th>Posledné prihlásenie</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {users.isLoading && (
              <Table.Tr><Table.Td colSpan={4}><Text c="dimmed" size="sm">Načítavam…</Text></Table.Td></Table.Tr>
            )}
            {users.data?.map((user) => (
              <Table.Tr key={user.id}>
                <Table.Td>{user.email}{user.id === currentUserId && <Badge ml="xs" size="xs">vy</Badge>}</Table.Td>
                <Table.Td>
                  <Select size="xs" data={ROLE_OPTIONS} value={user.role} allowDeselect={false} w={210}
                          aria-label={`Rola ${user.email}`}
                          onChange={(value) => value && update.mutate({ id: user.id, role: value as Role })} />
                </Table.Td>
                <Table.Td>
                  <Switch checked={user.is_active} aria-label={`Aktívny ${user.email}`}
                          onChange={(e) => update.mutate({ id: user.id, is_active: e.currentTarget.checked })} />
                </Table.Td>
                <Table.Td>{formatDate(user.last_login_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <CreateUserModal opened={opened} onClose={close} />
    </Paper>
  );
}

export function SettingsPage() {
  const { data: session } = useSession();
  if (!session) return null;
  return (
    <Stack>
      <Title order={1} size="h2">Nastavenia</Title>
      {session.user.role === "admin" ? (
        <UsersSection currentUserId={session.user.id} />
      ) : (
        <Paper withBorder p="md" radius="md">
          <Text>Prihlásený ako {session.user.email}. Nastavenia môže meniť iba administrátor.</Text>
        </Paper>
      )}
      <PasswordSection />
      <SchedulingSettingsSection readOnly={session.user.role !== "admin"} />
      <CatalogSyncSection readOnly={session.user.role !== "admin"} />
      <AlertRulesSection readOnly={session.user.role !== "admin"} />
    </Stack>
  );
}
