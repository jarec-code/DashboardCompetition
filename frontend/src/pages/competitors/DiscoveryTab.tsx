import {
  Alert, Anchor, Badge, Button, Checkbox, Collapse, Group, List, NumberInput, Paper, SegmentedControl, Select, SimpleGrid, Stack,
  Table, Text, Textarea, TextInput, Title,
} from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconPlayerPlay, IconSearch } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  useCatalog, useDiscovery, useDiscoveryUrls, useItemAction, useRunDiscovery, useSaveDiscovery,
  type CatalogItem, type Diagnosis, type DiscoveryMode, type DiscoveryRun, type DiscoveryState, type ItemMatch,
  type MethodQuality, type Recipe, type SourceType, type Strategy,
} from "../../api/discovery";
import { useProducts } from "../../api/products";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime, formatMoney } from "../../format";

const MODES: { value: DiscoveryMode; label: string }[] = [
  { value: "off", label: "Vypnuté" },
  { value: "shadow", label: "Tieňový režim – hľadá a páruje, nič nesleduje" },
  { value: "auto", label: "Automatický – spárované produkty sa sledujú" },
];
const SOURCES: Record<SourceType, string> = {
  sitemap: "sitemap", feed: "produktový feed", listing: "stránky so zoznamom produktov", adapter: "zoznam produktov (adaptér webu)",
  crawl: "prechádzanie webu",
};
const RUN_STATUS: Record<DiscoveryRun["status"], { label: string; color: string }> = {
  running: { label: "prebieha", color: "blue" }, succeeded: { label: "dokončený", color: "green" },
  failed: { label: "zlyhal", color: "red" }, stopped: { label: "zastavený", color: "orange" },
  partial: { label: "čiastočne", color: "yellow" },
};
const STRATEGIES: { value: Strategy; label: string }[] = [
  { value: "auto", label: "Automaticky – adaptér alebo feed, sitemap, prechádzanie webu" },
  { value: "adapter", label: "Iba adaptér webu" },
  { value: "feed", label: "Iba produktový feed" },
  { value: "sitemap", label: "Iba sitemap" },
  { value: "crawl", label: "Iba prechádzanie webu" },
];
const lines = (values: string[]) => values.join("\n");
const split = (text: string, sep: RegExp) => text.split(sep).map((v) => v.trim()).filter(Boolean);

function recipeForm(s: DiscoveryState["settings"]) {
  return { strategy: s.strategy ?? "auto", feed_url: s.feed_url ?? "", entry_urls: lines(s.entry_urls ?? []),
           extra_domains: (s.extra_domains ?? []).join(", "), include_patterns: lines(s.include_patterns ?? []),
           exclude_patterns: lines(s.exclude_patterns ?? []), product_pattern: s.product_pattern ?? "",
           drop_params: (s.drop_params ?? []).join(", ") };
}

function recipeValue(f: ReturnType<typeof recipeForm>): Recipe {
  return { strategy: f.strategy, feed_url: f.feed_url.trim() || null, entry_urls: split(f.entry_urls, /\n/),
           extra_domains: split(f.extra_domains, /[,\s]+/), include_patterns: split(f.include_patterns, /\n/),
           exclude_patterns: split(f.exclude_patterns, /\n/), product_pattern: f.product_pattern.trim() || null,
           drop_params: split(f.drop_params, /[,\s]+/) };
}

const FETCH: Record<string, string> = {
  ok: "načítané", no_price: "bez ceny", not_product: "nie je produkt", failed: "chyba", skipped: "preskočené",
};
const KINDS: Record<string, string> = {
  product: "produkty", listing: "zoznamy", category: "kategórie", info: "ostatné", unknown: "nezaradené",
};
const TYPES: Record<string, string> = {
  metodika: "metodika", pracovni_ucebnice: "pracovná učebnica", pracovni_sesit: "pracovný zošit",
  pracovni_listy: "pracovné listy", pisanka: "písanka", citanka: "čítanka", slabikar: "slabikár", abeceda: "abeceda",
  ucebnice: "učebnica",
};
const METHODS: Record<string, string> = {
  identifier: "ISBN/EAN", attributes: "automaticky – porovnateľný titul", manual: "ručne", existing: "existujúca ponuka",
};
const FILTERS: { value: string; label: string; match?: ItemMatch; gone?: boolean }[] = [
  { value: "review", label: "Na kontrolu", match: "review" },
  { value: "matched", label: "Spárované", match: "matched" },
  { value: "new", label: "Nové", match: "new" },
  { value: "unrelated", label: "Nesúvisiace", match: "unrelated" },
  { value: "ignored", label: "Ignorované", match: "ignored" },
  { value: "gone", label: "Zmiznuté", gone: true },
  { value: "incomplete", label: "Neúplné" },
];
const PAGE_SIZE = 50;

export function DiscoveryTab({ competitorId, readOnly }: { competitorId: number; readOnly: boolean }) {
  const state = useDiscovery(competitorId);
  const queryClient = useQueryClient();
  // When a run finishes, the catalogue and the tracked offers have changed.
  const busy = Boolean(state.data?.settings.run_requested || state.data?.runs[0]?.status === "running");
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      ["catalog", "offers", "comparison"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
    }
    wasBusy.current = busy;
  }, [busy, queryClient]);
  if (state.error) return <Alert color="red">{state.error.message}</Alert>;
  if (!state.data) return null;
  return (
    <Stack>
      <SettingsCard competitorId={competitorId} state={state.data} readOnly={readOnly} />
      <Runs runs={state.data.runs} />
      <Diagnostics competitorId={competitorId} state={state.data} />
      <Items competitorId={competitorId} state={state.data} readOnly={readOnly} />
    </Stack>
  );
}

function SettingsCard({ competitorId, state, readOnly }: { competitorId: number; state: DiscoveryState; readOnly: boolean }) {
  const save = useSaveDiscovery(competitorId);
  const run = useRunDiscovery(competitorId);
  const s = state.settings;
  const [mode, setMode] = useState<DiscoveryMode>(s.mode);
  const [terms, setTerms] = useState(Boolean(s.terms_confirmed_at));
  const [numbers, setNumbers] = useState({ interval_days: s.interval_days, budget_per_night: s.budget_per_night,
                                           spacing_seconds: s.spacing_seconds, max_pages: s.max_pages,
                                           max_depth: s.max_depth, max_run_days: s.max_run_days });
  const [recipeOpen, setRecipeOpen] = useState(false);
  const [recipe, setRecipe] = useState(() => recipeForm(s));
  useEffect(() => { setMode(s.mode); setTerms(Boolean(s.terms_confirmed_at)); }, [s.mode, s.terms_confirmed_at]);

  function submit(event: FormEvent, extra: { redetect?: boolean } = {}) {
    event.preventDefault();
    save.mutate({ mode, terms_confirmed: terms, ...numbers, ...extra, recipe: recipeValue(recipe) },
                { onSuccess: (st) => { setRecipe(recipeForm(st.settings));
                                       notifications.show({ color: "green", message: "Nastavenie objavovania bolo uložené." }); } });
  }
  const r = (key: keyof typeof recipe) => ({
    value: recipe[key], disabled: readOnly,
    onChange: (e: { currentTarget: { value: string } }) => setRecipe({ ...recipe, [key]: e.currentTarget.value }),
  });

  const waiting = s.run_requested || state.runs[0]?.status === "running";
  return (
    <Paper withBorder p="md" radius="md">
      <form onSubmit={submit}>
        <Stack>
          <div>
            <Title order={3} size="h4">Automatické objavovanie produktov</Title>
            <Text size="xs" c="dimmed">Raz za interval (v nočnom okne) prejde zoznam produktov konkurenta, nové produkty
              spáruje s porovnateľnými vlastnými a zmiznuté označí. Iba verejné stránky, robots.txt sa rešpektuje, jedna
              požiadavka naraz s rozostupom, denný limit požiadaviek. Pri obmedzení prístupu webom sa objavovanie na 7 dní
              pozastaví.</Text>
          </div>
          {(save.error || run.error) && <Alert color="red">{(save.error ?? run.error)?.message}</Alert>}
          {s.paused_until && new Date(s.paused_until) > new Date() && (
            <Alert color="orange" title="Objavovanie je pozastavené">Do {formatDateTime(s.paused_until)}. {s.last_error}</Alert>
          )}
          {!s.paused_until && s.last_error && <Alert color="orange">{s.last_error}</Alert>}
          <Select label="Režim" data={MODES} value={mode} allowDeselect={false} disabled={readOnly} w={420}
                  onChange={(v) => setMode((v as DiscoveryMode) ?? "off")} />
          <Checkbox disabled={readOnly} checked={terms} onChange={(e) => setTerms(e.currentTarget.checked)}
                    label="Skontroloval(a) som obchodné podmienky webu konkurenta: automatizované čítanie verejných stránok nie je zakázané."
                    description={s.terms_confirmed_at ? `Potvrdil ${s.terms_confirmed_by ?? "—"} ${formatDateTime(s.terms_confirmed_at)}` : undefined} />
          <SimpleGrid cols={{ base: 1, sm: 3 }} maw={640}>
            <NumberInput label="Interval (dni)" min={1} max={60} disabled={readOnly} value={numbers.interval_days}
                         onChange={(v) => setNumbers({ ...numbers, interval_days: Number(v) || 7 })} />
            <NumberInput label="Požiadavky za noc" min={10} max={1000} disabled={readOnly} value={numbers.budget_per_night}
                         onChange={(v) => setNumbers({ ...numbers, budget_per_night: Number(v) || 120 })} />
            <NumberInput label="Rozostup (s)" min={5} max={300} disabled={readOnly} value={numbers.spacing_seconds}
                         onChange={(v) => setNumbers({ ...numbers, spacing_seconds: Number(v) || 10 })} />
            <NumberInput label="Stránok na beh" min={10} max={20000} disabled={readOnly} value={numbers.max_pages}
                         description="pri prechádzaní webu"
                         onChange={(v) => setNumbers({ ...numbers, max_pages: Number(v) || 400 })} />
            <NumberInput label="Hĺbka odkazov" min={1} max={10} disabled={readOnly} value={numbers.max_depth}
                         onChange={(v) => setNumbers({ ...numbers, max_depth: Number(v) || 4 })} />
            <NumberInput label="Najdlhší beh (dni)" min={1} max={60} disabled={readOnly} value={numbers.max_run_days}
                         onChange={(v) => setNumbers({ ...numbers, max_run_days: Number(v) || 14 })} />
          </SimpleGrid>
          <div>
            <Text size="sm" fw={500}>Zdroj produktov</Text>
            {s.source ? (
              <Text size="sm">{SOURCES[s.source.type]}{s.source.detected ? " (nájdený automaticky)" : " (zadaný ručne)"}:{" "}
                {s.source.urls.map((u) => <Anchor key={u} href={u} target="_blank" rel="noopener noreferrer" size="sm" mr="xs">{u}</Anchor>)}
              </Text>
            ) : <Text size="sm" c="dimmed">Zatiaľ nerozpoznaný – pri najbližšom behu sa vyskúšajú adaptér, feed, sitemap a prechádzanie webu.</Text>}
            {!readOnly && (
              <Group gap="xs" mt={4}>
                <Button size="compact-xs" variant="subtle" onClick={() => setRecipeOpen((o) => !o)}>Recept webu</Button>
                {s.source && <Button size="compact-xs" variant="subtle"
                                     onClick={(e) => submit(e as unknown as FormEvent, { redetect: true })}>Rozpoznať znova</Button>}
              </Group>
            )}
            <Collapse expanded={recipeOpen || Boolean(readOnly && s.strategy !== "auto")}>
              <Stack gap="xs" mt="xs" maw={760}>
                <Text size="xs" c="dimmed">Recept upresní, kde a ako hľadať produkty, ak automatika nestačí. Zmena receptu spôsobí nové
                  rozpoznanie pri najbližšom behu. Pri ďalšej doméne platí potvrdenie podmienok aj pre ňu.</Text>
                <Select label="Spôsob načítania" w={420} allowDeselect={false} value={recipe.strategy} disabled={readOnly}
                        data={STRATEGIES} onChange={(v) => setRecipe({ ...recipe, strategy: (v as Strategy) ?? "auto" })} />
                <TextInput label="Adresa produktového feedu (Heureka, Google)" placeholder="https://…" {...r("feed_url")} />
                <Textarea label="Vstupné stránky na prechádzanie (jedna na riadok)" autosize minRows={2} {...r("entry_urls")}
                          placeholder="https://www.example.cz/katalog" />
                <TextInput label="Ďalšie domény (čiarkou)" placeholder="naseucebnice.cz" {...r("extra_domains")}
                           description="napr. obchod konkurenta na inej doméne" />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <Textarea label="Prechádzať iba adresy (regulárne výrazy, jeden na riadok)" autosize minRows={2}
                            placeholder="^/katalog" {...r("include_patterns")} />
                  <Textarea label="Nikdy neprechádzať (regulárne výrazy)" autosize minRows={2} placeholder="^/blog"
                            {...r("exclude_patterns")} />
                </SimpleGrid>
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput label="Vzor adresy produktu (regulárny výraz)" placeholder="^/produkt/\d+$" {...r("product_pattern")} />
                  <TextInput label="Ignorované parametre adresy (čiarkou)" placeholder="sort, page_size" {...r("drop_params")} />
                </SimpleGrid>
              </Stack>
            </Collapse>
          </div>
          <Text size="sm" c="dimmed">
            Posledný úspešný beh: {formatDateTime(s.last_success_at)} · ďalší: {s.mode === "off" ? "—" : formatDateTime(s.next_run_at)}
            {s.mode === "shadow" && ` · tieňový režim: ${s.shadow_ok_runs} z 2 úspešných behov pred prechodom do automatiky`}
            {` · dnes ${s.budget_used} z ${s.budget_per_night} požiadaviek`}
          </Text>
          {!readOnly && (
            <Group justify="flex-end">
              <Button variant="default" leftSection={<IconPlayerPlay size={16} />} loading={run.isPending || waiting}
                      disabled={s.mode === "off" || !s.terms_confirmed_at} onClick={() => run.mutate()}>Spustiť teraz</Button>
              <Button type="submit" loading={save.isPending}>Uložiť</Button>
            </Group>
          )}
        </Stack>
      </form>
    </Paper>
  );
}

function stat(run: DiscoveryRun, key: string) {
  const value = run.stats[key];
  return typeof value === "number" ? value : "—";
}

function Runs({ runs }: { runs: DiscoveryRun[] }) {
  if (!runs.length) return null;
  return (
    <Paper withBorder p="md" radius="md">
      <Title order={3} size="h4" mb="xs">Posledné behy</Title>
      <Table.ScrollContainer minWidth={820}>
        <Table striped verticalSpacing={4}>
          <Table.Thead><Table.Tr>
            <Table.Th>Začiatok</Table.Th><Table.Th>Druh</Table.Th><Table.Th>Stav</Table.Th><Table.Th ta="right">Požiadavky</Table.Th>
            <Table.Th ta="right">Stránky</Table.Th><Table.Th ta="right">Nájdené</Table.Th><Table.Th ta="right">S cenou</Table.Th>
            <Table.Th ta="right">Nové</Table.Th><Table.Th ta="right">Zmiznuté</Table.Th>
            <Table.Th ta="right">Spárované</Table.Th><Table.Th ta="right">Na kontrolu</Table.Th><Table.Th>Poznámka</Table.Th>
          </Table.Tr></Table.Thead>
          <Table.Tbody>
            {runs.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>{formatDateTime(r.started_at)}</Table.Td>
                <Table.Td>{r.kind === "prices" ? "ceny" : r.mode === "shadow" ? "objavovanie (tieňové)" : "objavovanie"}</Table.Td>
                <Table.Td><Badge variant="light" color={RUN_STATUS[r.status].color} style={{ minWidth: "max-content" }}>
                  {RUN_STATUS[r.status].label}</Badge></Table.Td>
                <Table.Td ta="right">{r.requests}</Table.Td>
                <Table.Td ta="right">{stat(r, "pages")}</Table.Td>
                <Table.Td ta="right">{stat(r, "found")}</Table.Td>
                <Table.Td ta="right">{stat(r, "priced")}</Table.Td>
                <Table.Td ta="right">{stat(r, "new")}</Table.Td>
                <Table.Td ta="right">{stat(r, "gone")}</Table.Td>
                <Table.Td ta="right">{stat(r, "matched")}</Table.Td>
                <Table.Td ta="right">{stat(r, "review")}</Table.Td>
                <Table.Td><Text size="xs" c={r.error ? "red" : r.status === "partial" ? "orange" : "dimmed"}>
                  {r.error ?? [...(r.stats.incomplete ?? []),
                               r.source_type ? `zdroj: ${SOURCES[r.source_type]}` : null,
                               r.stats.promoted ? "prepnuté do automatiky" : null,
                               typeof r.stats.tracked_new === "number" && r.stats.tracked_new ? `nové sledované: ${r.stats.tracked_new}` : null,
                               typeof r.stats.price_changes === "number" ? `zmeny cien: ${r.stats.price_changes}` : null,
                               r.stats.quality_note as string | null].filter(Boolean).join(" · ")}
                </Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

/** How products were looked for, what the crawler learned about the website and what it could not process. */
function Diagnostics({ competitorId, state }: { competitorId: number; state: DiscoveryState }) {
  const last = state.runs.find((r) => r.kind === "discovery");
  const crawled = state.settings.source?.type === "crawl" || Boolean(last?.stats.templates);
  const urls = useDiscoveryUrls(competitorId, crawled);
  if (!last) return null;
  const attempts = last.stats.attempts ?? [];
  const templates = Object.entries(last.stats.templates ?? {});
  const u = urls.data;
  const d = state.settings.diagnosis as Diagnosis;
  const methods = Object.entries(d?.methods ?? {}) as [SourceType, MethodQuality][];
  if (!attempts.length && !templates.length && !u?.problems.length && !methods.length) return null;
  return (
    <Paper withBorder p="md" radius="md">
      <Title order={3} size="h4" mb="xs">Diagnostika zdroja</Title>
      <Stack gap="sm">
        {(d?.suggestions ?? []).map((text, i) => <Alert key={i} color="blue" p="xs">{text}</Alert>)}
        {methods.length > 0 && (
          <div>
            <Text size="sm" fw={500}>Spôsoby načítania {d.at && <Text span size="xs" c="dimmed">(rozpoznané {formatDateTime(d.at)}
              {d.stale ? ", pri ďalšom behu sa rozpoznajú znova" : ""})</Text>}</Text>
            <Table.ScrollContainer minWidth={600}>
              <Table verticalSpacing={2} fz="xs">
                <Table.Thead><Table.Tr><Table.Th>Spôsob</Table.Th><Table.Th ta="right">Produkty s cenou</Table.Th>
                  <Table.Th ta="right">Načítané</Table.Th><Table.Th ta="right">S cenou</Table.Th><Table.Th ta="right">S názvom</Table.Th>
                  <Table.Th ta="right">Vierohodné ceny</Table.Th><Table.Th>Výsledok</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{methods.map(([name, q]) => (
                  <Table.Tr key={name}>
                    <Table.Td>{SOURCES[name]}{d.chosen?.includes(name) && <Badge ml={6} size="xs" variant="light">používa sa</Badge>}</Table.Td>
                    <Table.Td ta="right">{q.found}</Table.Td><Table.Td ta="right">{q.read}</Table.Td>
                    <Table.Td ta="right">{Math.round(q.priced * 100)} %</Table.Td><Table.Td ta="right">{Math.round(q.titled * 100)} %</Table.Td>
                    <Table.Td ta="right">{Math.round(q.plausible * 100)} %</Table.Td>
                    <Table.Td><Text size="xs" c={q.sufficient ? "green" : "orange"}>{q.sufficient ? "dostatočné" : "nedostatočné"}
                      {q.reason ? ` – ${q.reason}` : ""}</Text></Table.Td>
                  </Table.Tr>
                ))}</Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </div>
        )}
        {attempts.length > 0 && (
          <div>
            <Text size="sm" fw={500}>Ako sa hľadali produkty (posledný beh)</Text>
            <List size="xs" spacing={2}>{attempts.map((a, i) => (
              <List.Item key={i}><b>{a.what}</b>: {a.result}</List.Item>))}</List>
          </div>
        )}
        {u && (
          <Text size="sm">Známe stránky webu: {Object.entries(u.by_kind).map(([k, n]) => `${KINDS[k] ?? k} ${n}`).join(", ") || "—"}
            {u.by_status.failed ? ` · zlyhané ${u.by_status.failed}` : ""}{u.by_status.skipped ? ` · preskočené ${u.by_status.skipped}` : ""}
            {u.by_status.queued ? ` · čakajú ${u.by_status.queued}` : ""}</Text>
        )}
        {templates.length > 0 && (
          <Table.ScrollContainer minWidth={600}>
            <Table verticalSpacing={2} fz="xs">
              <Table.Thead><Table.Tr><Table.Th>Typ stránok (šablóna URL)</Table.Th><Table.Th ta="right">Načítané</Table.Th>
                <Table.Th ta="right">Produkty</Table.Th><Table.Th ta="right">Zoznamy</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{templates.map(([t, v]) => (
                <Table.Tr key={t}><Table.Td ff="monospace">{t}</Table.Td><Table.Td ta="right">{v.pages}</Table.Td>
                  <Table.Td ta="right">{v.products}</Table.Td><Table.Td ta="right">{v.listings}</Table.Td></Table.Tr>
              ))}</Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
        {u && u.problems.length > 0 && (
          <div>
            <Text size="sm" fw={500}>Nespracované časti webu</Text>
            <List size="xs" spacing={2}>{u.problems.map((p, i) => (
              <List.Item key={i}><Text span ff="monospace" size="xs">{p.template}</Text> – {p.count}×{" "}
                {p.status === "failed" ? "zlyhalo" : "preskočené"}{p.reason ? `: ${p.reason}` : ""}
                {p.examples[0] && <> (napr. <Anchor href={p.examples[0]} target="_blank" rel="noopener noreferrer" size="xs">
                  {p.examples[0].replace(/^https?:\/\/[^/]+/, "")}</Anchor>)</>}
              </List.Item>
            ))}</List>
          </div>
        )}
      </Stack>
    </Paper>
  );
}

function describe(item: CatalogItem) {
  const a = item.attributes;
  const parts = [a.grades?.length ? `${a.grades.join(", ")}. roč.`.replace("MS. roč.", "MŠ").replace("SS. roč.", "SŠ") : null,
                 a.subjects?.join(", "), a.type ? TYPES[a.type] ?? a.type : null,
                 a.parts?.length ? `${a.parts.join(" a ")}. diel` : null, a.bundle ? "komplet" : null];
  return parts.filter(Boolean).join(" · ");
}

function ProductPicker({ onPick }: { onPick: (id: number) => void }) {
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search.trim(), 300);
  const products = useProducts({ q: debounced || undefined, bundles: "exclude", page_size: 10 }, debounced.length >= 2);
  return (
    <Select size="xs" w={260} placeholder="Priradiť iný produkt…" searchable searchValue={search} onSearchChange={setSearch}
            data={(products.data?.items ?? []).map((p) => ({ value: String(p.id), label: p.name }))}
            nothingFoundMessage={debounced.length >= 2 ? "Nič sa nenašlo" : "Napíšte aspoň 2 znaky"}
            onChange={(v) => v && onPick(Number(v))} />
  );
}

function Items({ competitorId, state, readOnly }: { competitorId: number; state: DiscoveryState; readOnly: boolean }) {
  const [filter, setFilter] = useState(state.counts.review ? "review" : "matched");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debounced] = useDebouncedValue(search.trim(), 300);
  const current = FILTERS.find((f) => f.value === filter)!;
  const list = useCatalog(competitorId, { match_status: current.match, item_status: current.gone ? "gone" : "active",
                                          incomplete: filter === "incomplete" || undefined,
                                          q: debounced || undefined, page, page_size: PAGE_SIZE });
  const action = useItemAction();
  const act = (id: number, kind: "assign" | "ignore" | "reset", productId?: number) =>
    action.mutate({ id, action: kind, productId }, {
      onError: (e) => notifications.show({ color: "red", message: e.message }),
    });

  const columns: Column<CatalogItem>[] = [
    { key: "title", label: "Produkt konkurenta", render: (i) => (
      <Stack gap={0}>
        <Anchor href={i.url} target="_blank" rel="noopener noreferrer" size="sm" fw={500}>{i.title ?? i.url}</Anchor>
        <Text size="xs" c="dimmed">{[i.hints.grade, i.price ? formatMoney(i.price, i.currency) : null].filter(Boolean).join(" · ")}</Text>
      </Stack>
    ) },
    { key: "attrs", label: "Rozpoznané", render: (i) => (
      <Stack gap={0}>
        <Text size="xs">{describe(i) || "—"}</Text>
        {(i.attributes.note || i.enrich_error) && <Text size="xs" c="dimmed">{i.enrich_error ?? i.attributes.note}</Text>}
        <Text size="xs" c="dimmed">
          {[i.found_by ? SOURCES[i.found_by] : null, i.fetch_status ? FETCH[i.fetch_status] : null,
            i.parsed_by ? `parser ${i.parsed_by}` : null,
            i.completeness !== null ? `úplnosť ${i.completeness} %` : null].filter(Boolean).join(" · ")}
        </Text>
      </Stack>
    ) },
    { key: "match", label: "Vlastný produkt", render: (i) => i.product_id ? (
      <Stack gap={0}>
        <Anchor component={Link} to={`/produkty/${i.product_id}`} size="sm">{i.product_name}</Anchor>
        <Text size="xs" c="dimmed">{i.match_method ? METHODS[i.match_method] : ""}{i.offer_id ? " · sleduje sa" : ""}</Text>
      </Stack>
    ) : i.match_status === "review" ? (
      <Stack gap={4}>
        {i.candidates.map((c) => (
          <Group key={c.product_id} gap={6} wrap="nowrap">
            {!readOnly && <Button size="compact-xs" variant="light" onClick={() => act(i.id, "assign", c.product_id)}>Priradiť</Button>}
            <Text size="xs">{c.name}{c.exact ? "" : " (čiastočná zhoda)"}</Text>
          </Group>
        ))}
      </Stack>
    ) : <Text size="sm" c="dimmed">—</Text> },
    ...(readOnly ? [] : [{ key: "actions", label: "", align: "right" as const, render: (i: CatalogItem) => (
      <Group gap={6} justify="flex-end" wrap="nowrap">
        {i.match_status !== "matched" && i.match_status !== "ignored" && i.status === "active" &&
          <ProductPicker onPick={(id) => act(i.id, "assign", id)} />}
        {i.match_status === "ignored" ? <Button size="compact-xs" variant="subtle" onClick={() => act(i.id, "reset")}>Obnoviť</Button>
          : <Button size="compact-xs" variant="subtle" color="gray" onClick={() => act(i.id, "ignore")}>
              {i.match_status === "matched" ? "Zrušiť párovanie" : "Ignorovať"}</Button>}
      </Group>
    ) }]),
  ];

  return (
    <Paper withBorder p="md" radius="md">
      <Title order={3} size="h4" mb="xs">Katalóg konkurenta</Title>
      <Group justify="space-between" mb="sm" align="flex-end">
        <SegmentedControl size="xs" value={filter} onChange={(v) => { setFilter(v); setPage(1); }}
                          data={FILTERS.map((f) => ({ value: f.value,
                            label: f.value === "incomplete" ? f.label
                              : `${f.label} (${(f.gone ? state.counts.gone : state.counts[f.match!]) ?? 0})` }))} />
        <TextInput size="xs" placeholder="Hľadať v názve" leftSection={<IconSearch size={14} />} w={220} value={search}
                   onChange={(e) => { setSearch(e.currentTarget.value); setPage(1); }} aria-label="Hľadať v katalógu" />
      </Group>
      <DataTable columns={columns} rows={list.data?.items} rowKey={(i) => i.id} total={list.data?.total} page={page}
                 pageSize={PAGE_SIZE} onPageChange={setPage} loading={list.isLoading} error={list.error} minWidth={900}
                 emptyText={state.runs.length ? "V tejto skupine nie sú žiadne produkty."
                   : "Katalóg je prázdny. Zapnite objavovanie a spustite prvý beh."} />
    </Paper>
  );
}
