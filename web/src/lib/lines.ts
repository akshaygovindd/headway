import { isHeavyRail } from "./map";

/** Brand fills for badges and bars only. Orange is never used as text on white. */
export const LINE_STYLE: Record<string, { code: string; fill: string; ink: string; name: string }> = {
  Red: { code: "RL", fill: "#DA291C", ink: "#FFFFFF", name: "Red Line" },
  Orange: { code: "OL", fill: "#ED8B00", ink: "#10141A", name: "Orange Line" },
  Blue: { code: "BL", fill: "#003DA5", ink: "#FFFFFF", name: "Blue Line" },
  "Green-B": { code: "B", fill: "#00843D", ink: "#FFFFFF", name: "Green Line B" },
  "Green-C": { code: "C", fill: "#00843D", ink: "#FFFFFF", name: "Green Line C" },
  "Green-D": { code: "D", fill: "#00843D", ink: "#FFFFFF", name: "Green Line D" },
  "Green-E": { code: "E", fill: "#00843D", ink: "#FFFFFF", name: "Green Line E" },
  Mattapan: { code: "M", fill: "#DA291C", ink: "#FFFFFF", name: "Mattapan Trolley" },
};

export function lineName(line: string): string {
  return LINE_STYLE[line]?.name ?? line;
}

export type LineKind = "heavy" | "light";
export function lineKind(line: string): LineKind {
  return isHeavyRail(line) ? "heavy" : "light";
}
