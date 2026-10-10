import type { Metadata } from "next";
import Link from "next/link";
import { RealtimeHeader } from "@/features/realtime/realtime-header";
import { sections } from "@/lib/navigation";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Quant OS | Mission Control",
  description: "Paper trading operations and deterministic risk state",
};

function Navigation() {
  return <nav aria-label="Primary navigation">{sections.map((item, index) => {
    const showGroup = index === 0 || sections[index - 1]?.group !== item.group;
    return <div key={item.href}>{showGroup && <p className="nav-group">{item.group}</p>}<Link className="nav-link" href={item.href}>{item.label}</Link></div>;
  })}</nav>;
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en" className="scheme-only-dark"><body>
    <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">AQ</span><span><strong>AI Quant OS</strong><small>MISSION CONTROL</small></span></Link><div className="topbar-center">PERSONAL TRADING OPERATIONS</div><RealtimeHeader /></header>
    <div className="app-frame"><aside className="sidebar"><Navigation /><div className="sidebar-foot"><span className="sidebar-foot-label">SYSTEM MODE</span><strong>Paper only</strong><p>Live routing disabled</p></div></aside><div className="main-area"><details className="mobile-nav"><summary>Menu</summary><Navigation /></details><main id="main-content">{children}</main><footer className="footer"><span>AI Quant OS · local development</span><span>Financial truth is backend owned</span></footer></div></div>
  </body></html>;
}
