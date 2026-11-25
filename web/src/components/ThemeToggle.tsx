"use client";

import { useState } from "react";

type Theme = "system" | "light" | "dark";
const ORDER: Theme[] = ["system", "light", "dark"];

function initial(): Theme {
  try {
    const v = window.localStorage.getItem("theme");
    if (v === "light" || v === "dark") return v;
  } catch {
    // storage blocked: fall back to the system setting
  }
  return "system";
}

function apply(t: Theme) {
  const root = document.documentElement;
  if (t === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", t);
  try {
    if (t === "system") window.localStorage.removeItem("theme");
    else window.localStorage.setItem("theme", t);
  } catch {
    // ignore
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => (typeof window === "undefined" ? "system" : initial()));
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length] ?? "system";
  return (
    <button
      type="button"
      onClick={() => {
        apply(next);
        setTheme(next);
      }}
      className="rounded border border-line px-2 py-1 text-sm"
      aria-label={`Colour theme: ${theme}. Switch to ${next}.`}
    >
      Theme: {theme}
    </button>
  );
}
