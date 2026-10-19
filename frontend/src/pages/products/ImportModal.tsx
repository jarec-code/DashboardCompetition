import { Code, Text } from "@mantine/core";

import { ImportWizard } from "../../components/ImportWizard";
import { formatMoney } from "../../format";

type Price = { market: string; price: string; currency: string };

export function ImportModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  return (
    <ImportWizard
      opened={opened} onClose={onClose} kind="products" title="Import vlastných produktov"
      help={<>
        Formáty: <b>CSV</b> (čiarka alebo bodkočiarka, UTF-8 alebo Windows-1250), <b>Excel</b> (.xlsx, prvý hárok) a
        <b> XML feed</b> (Heureka, Google Merchant). Prvý riadok tabuľky je hlavička: povinné stĺpce <Code>name</Code> a{" "}
        <Code>price</Code>, voliteľné <Code>market</Code>, <Code>currency</Code>, <Code>isbn</Code>, <Code>ean</Code>,{" "}
        <Code>sku</Code>, <Code>internal_id</Code>, <Code>brand</Code>, <Code>category</Code>, <Code>priority</Code>.
        Trh sa odvodí z meny (CZK → CZ, EUR → SK). Pred uložením uvidíte náhľad s výsledkom kontroly každého riadku;
        existujúce produkty sa neprepisujú.
      </>}
      describe={(r) => {
        const d = r.data as { name?: string; prices?: Price[]; isbn?: string; ean?: string; price?: string };
        const price = d.prices?.[0];
        return (
          <Text size="sm">
            {d.name ?? "—"}
            <Text span size="xs" c="dimmed">
              {price ? ` · ${price.market} ${formatMoney(price.price, price.currency)}` : d.price ? ` · ${d.price}` : ""}
              {d.isbn ? ` · ISBN ${d.isbn}` : d.ean ? ` · EAN ${d.ean}` : ""}
            </Text>
          </Text>
        );
      }}
    />
  );
}
