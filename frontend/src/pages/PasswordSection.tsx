import { Alert, Button, Group, Paper, PasswordInput, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api } from "../api/client";

/** Every user can change their own password; their other sessions end. */
export function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const change = useMutation({
    mutationFn: () => api<void>("/auth/password", { method: "POST",
                                                    body: { current_password: current, new_password: next } }),
    onSuccess: () => {
      setCurrent(""); setNext(""); setAgain("");
      notifications.show({ color: "green", message: "Heslo bolo zmenené. Ostatné prihlásenia boli ukončené." });
    },
  });
  const mismatch = again.length > 0 && next !== again;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!mismatch) change.mutate();
  }

  return (
    <Paper withBorder p="md" radius="md">
      <form onSubmit={submit}>
        <Stack>
          <Title order={2} size="h4">Moje heslo</Title>
          {change.error && <Alert color="red">{change.error.message}</Alert>}
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <PasswordInput label="Súčasné heslo" required autoComplete="current-password" value={current}
                           onChange={(e) => setCurrent(e.currentTarget.value)} />
            <PasswordInput label="Nové heslo" description="Aspoň 10 znakov" required minLength={10}
                           autoComplete="new-password" value={next} onChange={(e) => setNext(e.currentTarget.value)} />
            <PasswordInput label="Nové heslo znova" required autoComplete="new-password" value={again}
                           error={mismatch ? "Heslá sa nezhodujú" : undefined}
                           onChange={(e) => setAgain(e.currentTarget.value)} />
          </SimpleGrid>
          <Group justify="space-between">
            <Text size="xs" c="dimmed">Po zmene sa ukončia prihlásenia na ostatných zariadeniach.</Text>
            <Button type="submit" loading={change.isPending} disabled={mismatch}>Zmeniť heslo</Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}
