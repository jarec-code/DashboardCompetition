import { Alert, Button, Checkbox, Group, Loader, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { IconWorldSearch } from "@tabler/icons-react";
import { useState } from "react";

import { api } from "../../api/client";
import { useJobRunner, type Job } from "../../api/jobs";
import { useAddOffers } from "../../api/offers";

interface Candidate { url: string; text: string; score: number; tracked: boolean }

/** Reads product links from ONE listing page the user enters; the user picks which ones to track. */
export function DiscoverLinks({ competitorId, domain }: { competitorId: number; domain: string }) {
  const [url, setUrl] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const add = useAddOffers(competitorId);
  const runner = useJobRunner(
    () => api<Job>(`/competitors/${competitorId}/discover`, { method: "POST", body: { url, variant: "" } }),
    (job) => setPicked(((job.result as { candidates?: Candidate[] } | null)?.candidates ?? [])
      .filter((c) => !c.tracked).map((c) => c.url)),
  );
  const job = runner.job;
  const candidates = (job?.result as { candidates?: Candidate[] } | null)?.candidates ?? [];

  return (
    <Stack gap="xs">
      <Group align="flex-end">
        <TextInput label="Stránka kategórie alebo výpis produktov" placeholder={`https://www.${domain}/kategoria`}
                   description="Načíta sa iba táto jedna stránka (rešpektuje robots.txt). Web sa neprechádza celý."
                   w={460} value={url} onChange={(e) => setUrl(e.currentTarget.value)} />
        <Button variant="default" leftSection={<IconWorldSearch size={16} />} loading={runner.running}
                disabled={!url.trim()} onClick={runner.run}>Načítať odkazy</Button>
      </Group>
      {runner.error && <Alert color="red">{runner.error.message}</Alert>}
      {runner.running && <Group gap="xs"><Loader size="xs" /><Text size="sm">Načítavam stránku…</Text></Group>}
      {job && !runner.running && job.status !== "succeeded" && <Alert color="red">{job.last_error}</Alert>}
      {job?.status === "succeeded" && candidates.length === 0 && (
        <Text size="sm" c="dimmed">Na stránke sa nenašli odkazy, ktoré vyzerajú ako produktové stránky.</Text>
      )}
      {candidates.length > 0 && (
        <>
          <Group justify="space-between">
            <Text size="sm">Nájdené odkazy: {candidates.length}, vybrané: {picked.length}</Text>
            <Group gap="xs">
              <Button size="compact-xs" variant="subtle"
                      onClick={() => setPicked(candidates.filter((c) => !c.tracked).map((c) => c.url))}>Vybrať všetky</Button>
              <Button size="compact-xs" variant="subtle" onClick={() => setPicked([])}>Zrušiť výber</Button>
            </Group>
          </Group>
          <ScrollArea.Autosize mah={260} type="auto">
            <Checkbox.Group value={picked} onChange={setPicked}>
              <Stack gap={4}>
                {candidates.map((c) => (
                  <Checkbox key={c.url} value={c.url} disabled={c.tracked}
                            label={<Text size="sm">{c.text || new URL(c.url).pathname}{" "}
                              <Text span size="xs" c="dimmed">{new URL(c.url).pathname}{c.tracked ? " · už sledované" : ""}</Text></Text>} />
                ))}
              </Stack>
            </Checkbox.Group>
          </ScrollArea.Autosize>
          {add.data && (
            <Alert color="green">Pridané: {add.data.created.length}, duplicity: {add.data.duplicates.length},
              neplatné: {add.data.invalid.length}</Alert>
          )}
          <Group justify="flex-end">
            <Button disabled={picked.length === 0} loading={add.isPending}
                    onClick={() => add.mutate({ urls: picked, variant: "" }, { onSuccess: () => setPicked([]) })}>
              Pridať vybrané ({picked.length})
            </Button>
          </Group>
        </>
      )}
    </Stack>
  );
}
