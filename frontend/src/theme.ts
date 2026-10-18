import { createTheme } from "@mantine/core";

/** Single place for the design system: colors, radius, typography. */
export const theme = createTheme({
  primaryColor: "blue",
  defaultRadius: "md",
  fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  headings: { fontWeight: "650" },
});
