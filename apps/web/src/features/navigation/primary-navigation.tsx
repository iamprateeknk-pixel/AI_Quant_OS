"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isActiveNavigationItem, sections } from "@/lib/navigation";

function Navigation({ pathname }: { pathname: string | null }) {
  return <nav aria-label="Primary navigation">{sections.map((item, index) => {
    const showGroup = index === 0 || sections[index - 1]?.group !== item.group;
    const active = isActiveNavigationItem(pathname, item.href);
    return <div key={item.href}>{showGroup && <p className="nav-group">{item.group}</p>}<Link aria-current={active ? "page" : undefined} className={`nav-link${active ? " nav-link-active" : ""}`} href={item.href}>{item.label}</Link></div>;
  })}</nav>;
}

export function PrimaryNavigation() {
  return <Navigation pathname={usePathname()} />;
}

export function PrimaryNavigationFallback() {
  return <Navigation pathname={null} />;
}
