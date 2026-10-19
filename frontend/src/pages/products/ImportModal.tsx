import { Code, Text } from "@mantine/core";

import { ImportWizard } from "../../components/ImportWizard";
import { formatMoney } from "../../format";

type Price = { market: string; price: string; currency: string };

export function ImportModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  return (
    <ImportWizard
      opened={opened} onClose={onClose} kind="products" title="Import vlastných produktov" withMarket
      help={<>
        Formáty: <b>CSV</b> (čiarka alebo bodkočiarka, UTF-8 alebo Windows-1250), <b>Excel</b> (.xlsx, prvý hárok) a
        <b> XML feed</b> (Heureka, Google Merchant). Prvý riadok tabuľky je hlavička: povinný stĺpec <Code>name</Code>,
        ďalej <Code>price</Code>, <Code>market</Code>, <Code>currency</Code>, <Code>isbn</Code>, <Code>ean</Code>,{" "}
        <Code>sku</Code>, <Code>internal_id</Code> (alebo <Code>ID</Code>), <Code>url</Code>, <Code>brand</Code>,{" "}
        <Code>category</Code>, <Code>availability</Code>, <Code>priority</Code>. Rozpozná aj export z vlastného systému
        (Název, ISBN, EAN, Cena Eshop, Druh produktu, Ročníky, Předměty).
        <br />Existujúci produkt sa nájde podľa interného ID, SKU, ISBN/EAN alebo URL a aktualizuje sa; podľa samotného
        názvu iba pri rovnakej cene, inak ide na kontrolu. Identifikátory sa iba dopĺňajú. Súbor neprepíše údaje
        z feedu e-shopu, iba doplní chýbajúce. Opakovaný import nevytvorí duplicity.
      </>}
      describe={(r) => {
        const d = r.data as { name?: string; prices?: Price[]; isbn?: string; ean?: string; price?: string;
                              internal_id?: string };
        const price = d.prices?.[0];
        return (
          <Text size="sm">
            {d.name ?? "—"}
            <Text span size="xs" c="dimmed">
              {price ? ` · ${price.market} ${formatMoney(price.price, price.currency)}` : d.price ? ` · ${d.price}` : ""}
              {d.isbn ? ` · ISBN ${d.isbn}` : d.ean ? ` · EAN ${d.ean}` : ""}
              {d.internal_id ? ` · ID ${d.internal_id}` : ""}
            </Text>
          </Text>
        );
      }}
    />
  );
}
