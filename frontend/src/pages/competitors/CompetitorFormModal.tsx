import {
  Alert, Anchor, Button, Divider, Group, Modal, NumberInput, SegmentedControl, Select, SimpleGrid, Stack, Text,
  Textarea, TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import {
  useCreateCompetitor, useUpdateCompetitor, type Competitor, type CompetitorInput, type CompetitorStatus,
  type CompetitorType, type SourceType,
} from "../../api/competitors";
import { COUNTRY_OPTIONS, SOURCE_LABELS, STATUS_LABELS, TYPE_LABELS } from "./labels";

interface Props {
  opened: boolean;
  onClose: () => void;
  /** Competitor to edit; a new one is created when omitted. */
  competitor?: Competitor;
}

interface FormState {
  name: string;
  base_url: string;
  country: string | null;
  market: string;
  competitor_type: CompetitorType;
  preferred_source: SourceType;
  notes: string;
  status: CompetitorStatus;
  default_frequency_hours: number | "";
  timeout_seconds: number | "";
  max_concurrency: number | "";
  max_retries: number | "";
  min_interval_seconds: number | "";
}

function initialState(c?: Competitor): FormState {
  return {
    name: c?.name ?? "",
    base_url: c?.base_url ?? "",
    country: c?.country ?? null,
    market: c && c.market !== c.country ? c.market ?? "" : "",
    competitor_type: c?.competitor_type ?? "direct",
    preferred_source: c?.preferred_source ?? "html",
    notes: c?.notes ?? "",
    status: c?.status ?? "active",
    default_frequency_hours: c?.default_frequency_hours ?? "",
    timeout_seconds: 20,
    max_concurrency: 1,
    max_retries: 3,
    min_interval_seconds: 5,
  };
}

const options = <T extends string>(labels: Record<T, string>) =>
  (Object.entries(labels) as [T, string][]).map(([value, label]) => ({ value, label }));

export function CompetitorFormModal({ opened, onClose, competitor }: Props) {
  const navigate = useNavigate();
  const create = useCreateCompetitor();
  const update = useUpdateCompetitor(competitor?.id ?? 0);
  const mutation = competitor ? update : create;
  const [form, setForm] = useState<FormState>(() => initialState(competitor));
  const [duplicateReason, setDuplicateReason] = useState("");

  useEffect(() => {
    if (opened) {
      setForm(initialState(competitor));
      setDuplicateReason("");
      create.reset();
      update.reset();
    }
  }, [opened, competitor]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const error = mutation.error as ApiError | null;
  const duplicate = error?.detail?.code === "duplicate_domain"
    ? error.detail.existing as { id: number; name: string } : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    const payload: CompetitorInput = {
      name: form.name,
      base_url: form.base_url,
      country: form.country,
      market: form.market || null,
      competitor_type: form.competitor_type,
      preferred_source: form.preferred_source,
      notes: form.notes || null,
      status: form.status,
      default_frequency_hours: form.default_frequency_hours === "" ? null : form.default_frequency_hours,
      duplicate_domain_reason: duplicateReason || undefined,
    };
    if (!competitor) {
      payload.scrape_config = {
        timeout_seconds: Number(form.timeout_seconds || 20),
        max_concurrency: Number(form.max_concurrency || 1),
        max_retries: Number(form.max_retries === "" ? 3 : form.max_retries),
        min_interval_seconds: Number(form.min_interval_seconds || 5),
      };
    }
    mutation.mutate(payload, {
      onSuccess: (saved) => {
        notifications.show({ color: "green", message: competitor ? "Zmeny boli uložené." : `Konkurent ${saved.name} bol pridaný.` });
        onClose();
        if (!competitor) navigate(`/konkurencia/${saved.id}`);
      },
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} size="lg" title={competitor ? "Upraviť konkurenta" : "Nový konkurent"}>
      <form onSubmit={submit}>
        <Stack>
          {error && !duplicate && <Alert color="red">{error.message}</Alert>}
          {duplicate && (
            <Alert color="yellow" title="Doména je už evidovaná">
              <Stack gap="xs">
                <Text size="sm">
                  {error!.message}{" "}
                  <Anchor component={Link} to={`/konkurencia/${duplicate.id}`} onClick={onClose}>
                    Otvoriť {duplicate.name}
                  </Anchor>
                </Text>
                <TextInput label="Dôvod samostatného záznamu" required value={duplicateReason}
                           placeholder="napr. samostatný B2B obchod"
                           onChange={(e) => setDuplicateReason(e.currentTarget.value)} />
              </Stack>
            </Alert>
          )}
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="Názov" required maxLength={200} value={form.name}
                       onChange={(e) => set("name", e.currentTarget.value)} />
            <TextInput label="Základná URL" required placeholder="https://www.alter.cz" value={form.base_url}
                       onChange={(e) => set("base_url", e.currentTarget.value)} />
            <Select label="Krajina" data={COUNTRY_OPTIONS} clearable searchable value={form.country}
                    onChange={(v) => set("country", v)} />
            <TextInput label="Trh" description="Kód trhu pre porovnanie cien, predvolene podľa krajiny"
                       placeholder={form.country ?? "napr. CZ"} value={form.market} maxLength={16}
                       onChange={(e) => set("market", e.currentTarget.value.toUpperCase())} />
            <Select label="Typ konkurenta" data={options(TYPE_LABELS)} allowDeselect={false}
                    value={form.competitor_type} onChange={(v) => v && set("competitor_type", v as CompetitorType)} />
            <NumberInput label="Frekvencia aktualizácie (hodiny)" description="Prázdne = podľa priority produktu"
                         min={1} max={720} value={form.default_frequency_hours}
                         onChange={(v) => set("default_frequency_hours", v === "" ? "" : Number(v))} />
          </SimpleGrid>
          <div>
            <Text size="sm" fw={500} mb={4}>Preferovaný zdroj dát</Text>
            <SegmentedControl fullWidth data={options(SOURCE_LABELS)} value={form.preferred_source}
                              onChange={(v) => set("preferred_source", v as SourceType)} />
          </div>
          {competitor && (
            <Select label="Stav" allowDeselect={false} value={form.status}
                    data={(Object.keys(STATUS_LABELS) as CompetitorStatus[])
                      .map((s) => ({ value: s, label: STATUS_LABELS[s].label }))}
                    onChange={(v) => v && set("status", v as CompetitorStatus)} />
          )}
          <Textarea label="Interná poznámka" autosize minRows={2} maxRows={6} maxLength={5000} value={form.notes}
                    onChange={(e) => set("notes", e.currentTarget.value)} />

          {!competitor && (
            <>
              <Divider label="Základné nastavenia scrapingu" labelPosition="left" />
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                <NumberInput label="Časový limit (s)" min={1} max={60} value={form.timeout_seconds}
                             onChange={(v) => set("timeout_seconds", v === "" ? "" : Number(v))} />
                <NumberInput label="Súbežnosť" min={1} max={4} value={form.max_concurrency}
                             onChange={(v) => set("max_concurrency", v === "" ? "" : Number(v))} />
                <NumberInput label="Opakovania" min={0} max={5} value={form.max_retries}
                             onChange={(v) => set("max_retries", v === "" ? "" : Number(v))} />
                <NumberInput label="Rozostup (s)" min={1} max={3600} value={form.min_interval_seconds}
                             onChange={(v) => set("min_interval_seconds", v === "" ? "" : Number(v))} />
              </SimpleGrid>
              <Text size="xs" c="dimmed">Parser a selektory nastavíte v detaile konkurenta.</Text>
            </>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Zrušiť</Button>
            <Button type="submit" loading={mutation.isPending}>{competitor ? "Uložiť" : "Pridať"}</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
