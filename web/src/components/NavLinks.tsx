"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const isCurrent = (path: string, href: string) => {
  if (href === "/") return path === "/" || path.startsWith("/lines/");
  if (href.startsWith("/#")) return false;
  return path === href || path.startsWith(`${href}/`);
};

/** The main navigation. The only client part is knowing the current page for its 2px underline. */
export function NavLinks({ items, repoUrl }: { items: readonly (readonly [string, string])[]; repoUrl: string }) {
  const path = usePathname() ?? "/";
  return (
    <ul className="nav-tabs text-sm font-medium">
      {items.map(([href, label]) => (
        <li key={href}>
          <Link href={href} className="nav-link" aria-current={isCurrent(path, href) ? "page" : undefined}>
            {label}
          </Link>
        </li>
      ))}
      <li>
        <a href={repoUrl} className="nav-link">
          GitHub
        </a>
      </li>
    </ul>
  );
}
