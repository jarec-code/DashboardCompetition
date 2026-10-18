import { Alert, Center, Group, Loader, Pagination, Table, Text, UnstyledButton } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconArrowsSort } from "@tabler/icons-react";
import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: string;
  /** API sort key; the column is sortable when set. */
  sortKey?: string;
  align?: "left" | "right" | "center";
  /** Keep the cell content on one line (badges, dates, buttons). */
  nowrap?: boolean;
  render: (row: T) => ReactNode;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[] | undefined;
  rowKey: (row: T) => string | number;
  total?: number;
  page?: number;
  pageSize?: number;
  sort?: string;
  onSortChange?: (sort: string) => void;
  onPageChange?: (page: number) => void;
  loading?: boolean;
  error?: Error | null;
  emptyText: ReactNode;
  minWidth?: number;
}

/** Shared table with server-side sorting and pagination. */
export function DataTable<T>({
  columns, rows, rowKey, total = 0, page = 1, pageSize = 25, sort, onSortChange, onPageChange,
  loading, error, emptyText, minWidth = 720,
}: Props<T>) {
  const pages = Math.max(1, Math.ceil(total / pageSize));

  function header(column: Column<T>) {
    if (!column.sortKey || !onSortChange) return column.label;
    const active = sort === column.sortKey || sort === `-${column.sortKey}`;
    const descending = sort === `-${column.sortKey}`;
    const Icon = !active ? IconArrowsSort : descending ? IconArrowDown : IconArrowUp;
    return (
      <UnstyledButton onClick={() => onSortChange(active && !descending ? `-${column.sortKey}` : column.sortKey!)}
                      aria-label={`Zoradiť podľa: ${column.label}`} fw={600} fz="sm">
        <Group gap={4} wrap="nowrap" justify={column.align === "right" ? "flex-end" : "flex-start"}>
          {column.label}
          <Icon size={14} stroke={1.6} opacity={active ? 1 : 0.4} />
        </Group>
      </UnstyledButton>
    );
  }

  return (
    <>
      {error && <Alert color="red" mb="sm">{error.message}</Alert>}
      <Table.ScrollContainer minWidth={minWidth}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              {columns.map((c) => (
                <Table.Th key={c.key} ta={c.align} aria-sort={sort === c.sortKey ? "ascending"
                  : sort === `-${c.sortKey}` ? "descending" : undefined}>
                  {header(c)}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {loading && !rows && (
              <Table.Tr><Table.Td colSpan={columns.length}><Center p="md"><Loader size="sm" /></Center></Table.Td></Table.Tr>
            )}
            {rows?.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={columns.length}><Text c="dimmed" ta="center" p="md">{emptyText}</Text></Table.Td>
              </Table.Tr>
            )}
            {rows?.map((row) => (
              <Table.Tr key={rowKey(row)}>
                {columns.map((c) => (
                  <Table.Td key={c.key} ta={c.align} style={c.nowrap ? { whiteSpace: "nowrap" } : undefined}>
                    {c.render(row)}
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {onPageChange && total > 0 && (
        <Group justify="space-between" mt="sm">
          <Text size="sm" c="dimmed">
            {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} z {total}
          </Text>
          {pages > 1 && <Pagination total={pages} value={page} onChange={onPageChange} size="sm" />}
        </Group>
      )}
    </>
  );
}
