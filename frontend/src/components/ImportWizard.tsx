import {
  Alert, Badge, Button, FileInput, Group, List, Modal, SegmentedControl, Stack, Table, Text,
} from "@mantine/core";
import { IconFileImport } from "@tabler/icons-react";
import { useEffect, useState, type ReactNode } from "react";

import { useCommitImport, useDiscardImport, useUploadImport, type ImportRow, type RowStatus } from "../api/imports";

const STATUS: Record<RowStatus, { label: string; color: string }> = {
  ok: { label: "v poriadku", color: "green" },
  warning: { label: "s upozornením", color: "yellow" },
  duplicate: { label: "duplicita", color: "gray" },
  error: { label: "chyba", color: "red" },
};

interface Props {
  opened: boolean;
  onClose: () => void;
  kind: "products" | "offers";
  title: string;
  help: ReactNode;
  competitorId?: number;
  /** Short summary of a validated row for the preview table. */
  describe: (row: ImportRow) => ReactNode;
}

/** Two-step import: upload -> preview with a status and reason per row -> confirm. */
export function ImportWizard({ opened, onClose, kind, title, help, competitorId, describe }: Props) {
  const upload = useUploadImport();
  const commit = useCommitImport();
  const discard = useDiscardImport();
  const [file, setFile] = useState<File | null>(null);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    if (opened) { setFile(null); setFilter("all"); upload.reset(); commit.reset(); }
  }, [opened]);

  const batch = upload.data;
  const result = commit.data;
  const toSave = batch ? batch.stats.ok + batch.stats.warning : 0;
  const rows = batch?.rows.filter((r) => filter === "all" || r.status !== "ok") ?? [];

  function close() {
    if (batch && !result && batch.status === "preview") discard.mutate(batch.id);  // abandoned preview
    onClose();
  }

  return (
    <Modal opened={opened} onClose={close} size="xl" title={title}>
      <Stack>
        {!batch && (
          <>
            <Text size="sm" component="div">{help}</Text>
            <FileInput label="Súbor" accept=".csv,.xlsx,.xml,text/csv,application/xml" clearable value={file}
                       onChange={setFile} placeholder="CSV, Excel (.xlsx) alebo XML feed" leftSection={<IconFileImport size={16} />} />
            {upload.error && <Alert color="red">{upload.error.message}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" onClick={close}>Zrušiť</Button>
              <Button disabled={!file} loading={upload.isPending}
                      onClick={() => file && upload.mutate({ file, kind, competitorId })}>Načítať náhľad</Button>
            </Group>
          </>
        )}

        {batch && !result && (
          <>
            <Group gap="xs">
              <Text size="sm">{batch.file_name} ({batch.file_format}):</Text>
              {(Object.keys(STATUS) as RowStatus[]).map((s) => (
                <Badge key={s} variant="light" color={STATUS[s].color}>{STATUS[s].label}: {batch.stats[s]}</Badge>
              ))}
            </Group>
            <SegmentedControl size="xs" value={filter} onChange={setFilter} w="fit-content"
                              data={[{ value: "all", label: "Všetky riadky" }, { value: "problems", label: "Iba problémy" }]} />
            <Table.ScrollContainer minWidth={640} mah={420} type="native">
              <Table striped verticalSpacing={4} stickyHeader>
                <Table.Thead><Table.Tr><Table.Th w={60}>Riadok</Table.Th><Table.Th w={130}>Stav</Table.Th>
                  <Table.Th>Údaje</Table.Th><Table.Th>Dôvod</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => (
                    <Table.Tr key={r.row}>
                      <Table.Td>{r.row}</Table.Td>
                      <Table.Td><Badge variant="light" color={STATUS[r.status].color}>{STATUS[r.status].label}</Badge></Table.Td>
                      <Table.Td>{describe(r)}</Table.Td>
                      <Table.Td><Text size="xs" c={r.status === "error" ? "red" : "dimmed"}>{r.reason}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            {commit.error && <Alert color="red">{commit.error.message}</Alert>}
            <Group justify="space-between">
              <Text size="xs" c="dimmed">Uložia sa riadky „v poriadku“ a „s upozornením“. Duplicity a chyby sa preskočia.</Text>
              <Group>
                <Button variant="default" onClick={close}>Zrušiť import</Button>
                <Button disabled={toSave === 0} loading={commit.isPending} onClick={() => commit.mutate(batch.id)}>
                  Importovať {toSave} {toSave === 1 ? "riadok" : toSave < 5 && toSave > 0 ? "riadky" : "riadkov"}
                </Button>
              </Group>
            </Group>
          </>
        )}

        {result && (
          <>
            <Alert color={result.errors || result.skipped_at_commit.length ? "yellow" : "green"} title="Import dokončený">
              <Text size="sm">Importované: <b>{result.imported}</b>, preskočené duplicity: <b>{result.duplicates}</b>,
                chybné riadky: <b>{result.errors}</b></Text>
              {result.skipped_at_commit.length > 0 && (
                <List size="xs" mt={4}>{result.skipped_at_commit.map((s) => (
                  <List.Item key={s.row}>Riadok {s.row}: {s.reason}</List.Item>))}</List>
              )}
            </Alert>
            <Group justify="flex-end"><Button onClick={onClose}>Zavrieť</Button></Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}
