"use client";

import { useEffect, useRef, useState } from "react";
import notes from "@/data/metric-notes.json";
import { definitionFor, docUrl, sqlUrl } from "@/lib/definitions";

type Info = {
  k: string;
  line?: string;
  n?: string;
  win?: string;
  ex?: string;
  cov?: string;
  label?: string;
  value?: string;
};

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

function read(b: HTMLElement): Info {
  const d = b.dataset;
  return { k: d.k ?? "generic", line: d.line, n: d.n, win: d.win, ex: d.ex, cov: d.cov, label: d.label, value: d.value };
}

const WINDOWS: Record<string, string> = { last_24h: "last 24 hours", last_7d: "last 7 days" };

/**
 * One "How this was measured" dialog for the whole page. Every number is a server-rendered
 * <button data-k=...>; this component listens for their clicks and fills a native <dialog>.
 * Keyboard: Enter or Space opens it, Tab stays inside, Escape closes it and focus goes back to the
 * number. At 480px and below it is a bottom sheet (globals.css).
 */
export function MetricPopover() {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [info, setInfo] = useState<Info | null>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const b = (e.target as Element | null)?.closest?.("button[data-k]") as HTMLElement | null;
      if (!b) return;
      opener.current = b;
      setInfo(read(b));
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  useEffect(() => {
    const d = dialog.current;
    if (!info || !d) return;
    if (!d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    }
    d.querySelector<HTMLElement>("button[data-close]")?.focus();
  }, [info]);

  function close() {
    const d = dialog.current;
    if (d?.open) {
      if (typeof d.close === "function") d.close();
      else d.removeAttribute("open");
    }
    setInfo(null);
    opener.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDialogElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  const def = definitionFor(info?.k);
  const note = info?.line ? (notes as Record<string, { text: string; href: string }>)[info.line] : undefined;
  return (
    <dialog
      ref={dialog}
      className="metric-sheet"
      aria-labelledby="metric-sheet-title"
      onKeyDown={onKeyDown}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      {info ? (
        <div className="p-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">How this was measured</p>
          <h2 id="metric-sheet-title" className="mt-1 text-xl">
            {info.label ?? def.label}
            {info.value ? <span className="ml-2 tabular-nums">{info.value}</span> : null}
          </h2>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted">Definition</dt>
            <dd>{def.what}</dd>
            <dt className="text-muted">Observations (n)</dt>
            <dd>{info.n ?? "not given for this figure"}</dd>
            <dt className="text-muted">Coverage</dt>
            <dd>{info.cov ? `${Math.round(Number(info.cov))}% of scheduled trips observed` : "not applicable: this counts Headway's own runs"}</dd>
            <dt className="text-muted">Window</dt>
            <dd>{info.win ? (WINDOWS[info.win] ?? info.win) : "last 24 hours"}</dd>
            <dt className="text-muted">Left out</dt>
            <dd>{info.ex ?? "trips with an unproven order and hours when our own capture had a gap"}</dd>
          </dl>
          {note ? (
            <p className="mt-3 rounded border border-[#D5D9E0] bg-[#F4F5F7] p-3 text-sm">
              {note.text} <a href={docUrl(note.href)}>See the measurement</a>
            </p>
          ) : null}
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <a href={docUrl(`docs/METRICS.md#${def.anchor}`)}>Definition in METRICS.md</a>
            <a href={sqlUrl(def.table)}>The SQL ({def.table})</a>
          </p>
          <button type="button" data-close className="mt-4 min-h-11 rounded border border-[#10141A] bg-white px-4 text-sm font-semibold" onClick={close}>
            Close
          </button>
        </div>
      ) : null}
    </dialog>
  );
}
