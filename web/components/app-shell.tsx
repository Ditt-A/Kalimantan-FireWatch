"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const nav = [
  { href: "/", label: "Overview" },
  { href: "/prioritization", label: "Prioritization" },
  { href: "/response-planner", label: "Response planner" },
  { href: "/evidence", label: "Evidence" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" href="/" aria-label="Kalimantan FireWatch home">
          <span className="brand-mark">◒</span><span>FIREWATCH</span>
        </Link>
        <nav aria-label="Primary navigation">
          {nav.map((item) => (
            <Link className={pathname === item.href ? "nav-link active" : "nav-link"} href={item.href} key={item.href}>{item.label}</Link>
          ))}
        </nav>
        <span className="status-pill"><i />Validated snapshot</span>
      </header>
      {children}
      <footer className="footer"><span>Kalimantan FireWatch</span><span>Decision support · not automatic dispatch</span></footer>
    </div>
  );
}
