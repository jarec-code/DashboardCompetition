/** Chart colors: the validated categorical palette (fixed order, separate dark steps), text and surface roles.
 *  Validated with the data-viz palette validator: light and dark both pass; three light slots are below 3:1
 *  against the surface, so the chart always ships direct labels (<= 4 series) and a table view. */
export const SERIES = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};

export const INK = {
  light: { primary: "#0b0b0b", secondary: "#52514e", grid: "#e6e5e1", surface: "#fcfcfb" },
  dark: { primary: "#f0efec", secondary: "#c3c2b7", grid: "#34332f", surface: "#1a1a19" },
};

/** Color follows the competitor (its stable slot), never its position in a filtered list. */
export function seriesColor(slot: number, scheme: "light" | "dark"): string {
  const palette = SERIES[scheme];
  return palette[slot % palette.length];
}

export const MAX_SERIES = SERIES.light.length;
