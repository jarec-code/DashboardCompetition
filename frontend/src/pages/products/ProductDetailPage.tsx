import {
  Alert, Anchor, Badge, Breadcrumbs, Button, Center, Group, Loader, Modal, Paper, SimpleGrid, Stack, Text, Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconChartLine, IconEdit, IconTrash } from "@tabler/icons-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useOffers, type Offer } from "../../api/offers";
import { useDeleteProduct, useProduct } from "../../api/products";
import { useIsAdmin } from "../../auth/auth";
import { DataTable, type Column } from "../../components/DataTable";
import { formatDateTime, formatMoney } from "../../format";
import { HistoryModal } from "../offers/HistoryModal";
import { AssignModal } from "../offers/AssignModal";
import { OfferActions, OfferPriceCell, OfferUrlCell } from "../offers/OfferCells";
import { METHOD_LABELS, PRIORITY_LABELS } from "./labels";
import { ProductFormModal } from "./ProductFormModal";

const VAT = { with_vat: "s DPH", without_vat: "bez DPH", unknown: "DPH neznáme" } as const;
const AVAILABILITY = { in_stock: "skladom", out_of_stock: "nedostupné", preorder: "predpredaj", unknown: "neznáma" } as const;
const SOURCE_KIND = { feed: "feed e-shopu", file: "importovaný súbor" } as const;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>{label}</Text>
      <Text size="sm" component="div">{children}</Text>
    </div>
  );
}

export function ProductDetailPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const isAdmin = useIsAdmin();
  const { data: product, isLoading, error } = useProduct(id);
  const offers = useOffers({ product_id: id, page_size: 100 });
  const remove = useDeleteProduct();
  const [editOpened, edit] = useDisclosure();
  const [confirmOpened, confirm] = useDisclosure();
  const [assigning, setAssigning] = useState<Offer | null>(null);
  const [history, setHistory] = useState<Offer | null>(null);

  if (isLoading) return <Center p="xl"><Loader /></Center>;
  if (error || !product) return <Alert color="red" title="Produkt sa nepodarilo načítať">{error?.message}</Alert>;
  const a = product.attributes;

  const columns: Column<Offer>[] = [
    { key: "competitor", label: "Konkurent", nowrap: true, render: (o) => (
      <Anchor component={Link} to={`/konkurencia/${o.competitor_id}`} size="sm">{o.competitor_name}</Anchor>
    ) },
    { key: "url", label: "Ponuka", render: (o) => <OfferUrlCell offer={o} /> },
    { key: "price", label: "Cena a dostupnosť", render: (o) => <OfferPriceCell offer={o} /> },
    { key: "method", label: "Párovanie", nowrap: true,
      render: (o) => (o.matching_method ? METHOD_LABELS[o.matching_method] : "—") },
    ...(isAdmin ? [{ key: "actions", label: "", align: "right" as const,
                     render: (o: Offer) => <OfferActions offer={o} onAssign={setAssigning} onHistory={setHistory} /> }] : []),
  ];

  return (
    <Stack>
      <Breadcrumbs>
        <Anchor component={Link} to="/produkty" size="sm">Produkty</Anchor>
        <Text size="sm" lineClamp={1}>{product.name}</Text>
      </Breadcrumbs>
      <Group justify="space-between" align="flex-start">
        <Group gap="sm">
          <Title order={1} size="h2">{product.name}</Title>
          <Badge variant="light" color={PRIORITY_LABELS[product.priority].color}>{PRIORITY_LABELS[product.priority].label}</Badge>
          {product.tracking_status === "paused" && <Badge variant="light" color="yellow">sledovanie pozastavené</Badge>}
        </Group>
        <Group gap="xs">
          <Button component={Link} to={`/historia?product=${product.id}`} variant="default"
                  leftSection={<IconChartLine size={16} />}>Cenová história</Button>
          {isAdmin && (<>
            <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={confirm.open}>Zmazať</Button>
            <Button leftSection={<IconEdit size={16} />} onClick={edit.open}>Upraviť</Button>
          </>)}
        </Group>
      </Group>

      <Paper withBorder p="md" radius="md">
        <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="lg">
          <Field label="ISBN">{product.isbn ?? "—"}</Field>
          <Field label="EAN">{product.ean ?? "—"}</Field>
          <Field label="SKU / interné ID">{product.sku ?? "—"} / {product.internal_id ?? "—"}</Field>
          <Field label="Vydavateľ / kategória">{product.brand ?? "—"} / {product.category ?? "—"}</Field>
          <Field label="Predmet / ročník / typ">{a.subject ?? "—"} / {a.grade ?? "—"} / {a.type ?? "—"}</Field>
          <Field label="Vlastné ceny">
            {product.prices.length === 0 ? "—" : product.prices.map((p) => (
              <div key={p.market}>{p.market}: <b>{formatMoney(p.price, p.currency)}</b> <Text span size="xs" c="dimmed">{VAT[p.vat_mode]}</Text></div>
            ))}
          </Field>
          <Field label="Vo vlastnom e-shope">
            {product.url ? <Anchor href={product.url} target="_blank" rel="noopener noreferrer" size="sm">
              {product.url.replace(/^https?:\/\/(www\.)?/, "")}</Anchor> : "—"}
            {product.image_url && <div><Anchor href={product.image_url} target="_blank" rel="noopener noreferrer"
                                               size="xs">obrázok</Anchor></div>}
          </Field>
          <Field label="Dostupnosť">{AVAILABILITY[product.availability]}</Field>
          <Field label="Zdroj údajov">
            {product.source_kind ? <>{product.source} <Text span size="xs" c="dimmed">({SOURCE_KIND[product.source_kind]})</Text></>
              : "zadané ručne"}
            {product.source_synced_at && <Text size="xs" c="dimmed">načítané {formatDateTime(product.source_synced_at)}</Text>}
          </Field>
          <Field label="Rok vydania">{a.year ?? "—"}</Field>
          <Field label="Naposledy upravený">{formatDateTime(product.updated_at)}</Field>
        </SimpleGrid>
      </Paper>

      <Paper withBorder p="md" radius="md">
        <Title order={2} size="h4" mb="sm">Konkurenčné ponuky ({offers.data?.total ?? 0})</Title>
        <DataTable columns={columns} rows={offers.data?.items} rowKey={(o) => o.id} loading={offers.isLoading}
                   error={offers.error} minWidth={760}
                   emptyText="K produktu zatiaľ nie je priradená žiadna ponuka. Priraďte ju v detaile konkurenta alebo v záložke Párovanie." />
      </Paper>

      <ProductFormModal opened={editOpened} onClose={edit.close} product={product} />
      <AssignModal offer={assigning} onClose={() => setAssigning(null)} />
      <HistoryModal offer={history} onClose={() => setHistory(null)} />
      <Modal opened={confirmOpened} onClose={confirm.close} title={`Zmazať ${product.name}?`}>
        <Text size="sm">Priradené ponuky konkurentov sa nezmažú: zostanú sledované aj s cenovou históriou a vrátia sa
          do párovania.</Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={confirm.close}>Zrušiť</Button>
          <Button color="red" loading={remove.isPending} onClick={() => remove.mutate(product.id, {
            onSuccess: () => { notifications.show({ message: "Produkt bol zmazaný." }); navigate("/produkty"); },
          })}>Zmazať</Button>
        </Group>
      </Modal>
    </Stack>
  );
}
