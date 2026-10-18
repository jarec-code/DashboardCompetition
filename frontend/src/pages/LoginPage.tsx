import { Alert, Button, Center, Paper, PasswordInput, Stack, TextInput, Title } from "@mantine/core";
import { IconAlertCircle } from "@tabler/icons-react";
import { useState, type FormEvent } from "react";

import { useLogin } from "../auth/auth";

export function LoginPage() {
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate({ email, password });
  }

  return (
    <Center mih="100vh" p="md" bg="var(--mantine-color-body)">
      <Paper withBorder shadow="sm" p="xl" radius="md" w="100%" maw={380}>
        <form onSubmit={submit}>
          <Stack>
            <Title order={2} size="h3">Sledovanie cien</Title>
            {login.error && (
              <Alert color="red" icon={<IconAlertCircle size={18} />} role="alert">
                {login.error.message}
              </Alert>
            )}
            <TextInput label="E-mail" type="email" autoComplete="username" required value={email}
                       onChange={(e) => setEmail(e.currentTarget.value)} />
            <PasswordInput label="Heslo" autoComplete="current-password" required value={password}
                           onChange={(e) => setPassword(e.currentTarget.value)} />
            <Button type="submit" loading={login.isPending} fullWidth>Prihlásiť sa</Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
