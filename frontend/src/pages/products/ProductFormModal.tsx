import {
  ActionIcon, Alert, Button, Divider, Group, Modal, Select, SimpleGrid, Stack, Text, TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import type { VatMode } from "../../api/competitors";
import { useSaveProduct, type Priority, type Product, type ProductInput, type TrackingStatus } from "../../api/products";
import { PRIORITY_LABELS } from "./labels";

interface PriceRow { market: string; price: string; currency: string; vat_mode: VatMode }

const DEFAULT_CURRENCY: Record<string, string> = { SK: "EUR", CZ: "CZK", PL: "PLN", HU: "HUF", AT: "EUR", DE: "EUR" };
const VAT_OPTIONS = [
  { value: "with_vat", label: "s DPH" }, { value: "without_vat", label: "bez DPH" }, { value: "unknown", label: "neznáme" },
];

function initial(p?: Product): ProductInput & { priceRows: PriceRow[] } {
  return {
    name: p?.name ?? "", internal_id: p?.internal_id ?? null, isbn: p?.isbn ?? null, ean: p?.ean ?? null,
    sku: p?.sku ?? null, brand: p?.brand ?? null, category: p?.category ?? null,
    attributes: { subject: p?.attributes.subject ?? null, grade: p?.attributes.grade ?? null,
                  type: p?.attributes.type ?? null },
    priority: p?.priority ?? "regular", tracking_status: p?.tracking_status ?? "active", prices: [],
    priceRows: p?.prices.map((x) => ({ market: x.market, price: x.price, currency: x.currency, vat_mode: x.vat_mode }))
      ?? [{ market: "SK", price: "", currency: "EUR", vat_mode: "with_vat" }],
  };
}

export function ProductFormModal({ opened, onClose, product }: { opened: boolean; onClose: () => void; product?: Product }) {
  const navigate = useNavigate();
  const save = useSaveProduct(product?.id);
  const [form, setForm] = useState(() => initial(product));

  useEffect(() => {
    if (opened) { setForm(initial(product)); save.reset(); }
  }, [opened, product]);

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));
  const text = (key: "name" | "internal_id" | "isbn" | "ean" | "sku" | "brand" | "category") => ({
    value: form[key] ?? "",
    onChange: (e: ChangeEvent<HTMLInputElement>) => set(key, e.currentTarget.value),
  });
  const attr = (key: "subject" | "grade" | "type") => ({
    value: form.attributes[key] ?? "",
    onChange: (e: ChangeEvent<HTMLInputElement>) =>
      set("attributes", { ...form.attributes, [key]: e.currentTarget.value }),
  });
  const setRow = (i: number, change: Partial<PriceRow>) =>
    set("priceRows", form.priceRows.map((row, j) => {
      if (j !== i) return row;
      const next = { ...row, ...change };
      if (change.market !== undefined) {
        next.market = change.market.toUpperCase();
        if (DEFAULT_CURRENCY[next.market] && (!row.currency || row.currency === DEFAULT_CURRENCY[row.market])) {
          next.currency = DEFAULT_CURRENCY[next.market];
        }
      }
      return next;
    }));

  function submit(event: FormEvent) {
    event.preventDefault();
    const blank = (v: string | null) => (v && v.trim() ? v.trim() : null);
    const payload: ProductInput = {
      name: form.name, internal_id: blank(form.internal_id), isbn: blank(form.isbn), ean: blank(form.ean),
      sku: blank(form.sku), brand: blank(form.brand), category: blank(form.category),
      attributes: { subject: blank(form.attributes.subject ?? null), grade: blank(form.attributes.grade ?? null),
                    type: blank(form.attributes.type ?? null) },
      priority: form.priority, tracking_status: form.tracking_status,
      prices: form.priceRows.filter((r) => r.market.trim() && r.price.trim()).map((r) => ({
        market: r.market.trim(), price: r.price.replace(",", ".").replace(/\s/g, ""), currency: blank(r.currency),
        vat_mode: r.vat_mode,
      })),
    };
    save.mutate(payload, {
      onSuccess: (saved) => {
        notifications.show({ color: "green", message: product ? "Produkt bol uložený." : `Produkt ${saved.name} bol pridaný.` });
        onClose();
        if (!product) navigate(`/produkty/${saved.id}`);
      },
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} size="lg" title={product ? "Upraviť produkt" : "Nový produkt"}>
      <form onSubmit={submit}>
        <Stack>
          {save.error && <Alert color="red">{save.error.message}</Alert>}
          <TextInput label="Názov" required maxLength={500} {...text("name")} />
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <TextInput label="ISBN" placeholder="978-…" {...text("isbn")} />
            <TextInput label="EAN" {...text("ean")} />
            <TextInput label="SKU" {...text("sku")} />
            <TextInput label="Interné ID" {...text("internal_id")} />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="Značka / vydavateľ" {...text("brand")} />
            <TextInput label="Kategória" {...text("category")} />
          </SimpleGrid>
          <Divider label="Parametre pre párovanie s konkurenciou" labelPosition="left" />
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="Predmet" placeholder="matematika" {...attr("subject")} />
            <TextInput label="Ročník" placeholder="2" {...attr("grade")} />
            <TextInput label="Typ" placeholder="pracovný zošit" {...attr("type")} />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <Select label="Priorita sledovania" allowDeselect={false} value={form.priority}
                    data={(Object.keys(PRIORITY_LABELS) as Priority[]).map((p) => ({
                      value: p, label: `${PRIORITY_LABELS[p].label} (${PRIORITY_LABELS[p].description})` }))}
                    onChange={(v) => v && set("priority", v as Priority)} />
            <Select label="Sledovanie" allowDeselect={false} value={form.tracking_status}
                    data={[{ value: "active", label: "Aktívne" }, { value: "paused", label: "Pozastavené" }]}
                    onChange={(v) => v && set("tracking_status", v as TrackingStatus)} />
          </SimpleGrid>

          <Divider label="Vlastné ceny podľa trhu" labelPosition="left" />
          {form.priceRows.map((row, i) => (
            <Group key={i} gap="xs" align="flex-end" wrap="nowrap">
              <TextInput label={i === 0 ? "Trh" : undefined} aria-label="Trh" w={80} maxLength={16} value={row.market}
                         onChange={(e) => setRow(i, { market: e.currentTarget.value })} />
              <TextInput label={i === 0 ? "Cena" : undefined} aria-label="Cena" inputMode="decimal" style={{ flex: 1 }}
                         value={row.price} onChange={(e) => setRow(i, { price: e.currentTarget.value })} />
              <TextInput label={i === 0 ? "Mena" : undefined} aria-label="Mena" w={80} maxLength={3} value={row.currency}
                         onChange={(e) => setRow(i, { currency: e.currentTarget.value.toUpperCase() })} />
              <Select label={i === 0 ? "DPH" : undefined} aria-label="DPH" w={120} data={VAT_OPTIONS}
                      allowDeselect={false} value={row.vat_mode}
                      onChange={(v) => v && setRow(i, { vat_mode: v as VatMode })} />
              <ActionIcon variant="subtle" color="red" size="lg" aria-label="Odstrániť cenu"
                          onClick={() => set("priceRows", form.priceRows.filter((_, j) => j !== i))}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          ))}
          <Group justify="space-between">
            <Button variant="subtle" size="xs" leftSection={<IconPlus size={14} />}
                    onClick={() => set("priceRows", [...form.priceRows,
                      { market: form.priceRows.some((r) => r.market === "CZ") ? "" : "CZ", price: "",
                        currency: form.priceRows.some((r) => r.market === "CZ") ? "" : "CZK", vat_mode: "with_vat" }])}>
              Pridať trh
            </Button>
            <Text size="xs" c="dimmed">Porovnáva sa s cenou konkurenta na rovnakom trhu.</Text>
          </Group>

          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>Zrušiť</Button>
            <Button type="submit" loading={save.isPending}>{product ? "Uložiť" : "Pridať"}</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
