"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { searchNavigationItems } from "@/lib/navigation";

export function NavigationCommand() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const results = searchNavigationItems(query);

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    inputRef.current?.focus();
    return () => dialog.close();
  }, [open]);

  useEffect(() => {
    function openFromShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      }
    }
    window.addEventListener("keydown", openFromShortcut);
    return () => window.removeEventListener("keydown", openFromShortcut);
  }, []);

  function close() {
    setOpen(false);
    setQuery("");
  }

  return <>
    <button type="button" className="navigation-command-trigger" onClick={() => setOpen(true)} aria-haspopup="dialog" aria-expanded={open}>
      <span>Search destinations</span><kbd>Ctrl K</kbd>
    </button>
    <dialog ref={dialogRef} className="navigation-command" aria-labelledby="navigation-command-title" onClose={close} onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
      <div className="navigation-command-heading"><h2 id="navigation-command-title">Go to a destination</h2><button type="button" onClick={close} aria-label="Close search">Esc</button></div>
      <label className="sr-only" htmlFor="navigation-command-query">Search destinations</label>
      <input ref={inputRef} id="navigation-command-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search pages or groups" autoComplete="off" />
      <ul aria-label="Destinations">{results.map((item) => <li key={item.href}><Link href={item.href} onClick={close}><span>{item.label}</span><small>{item.group}</small></Link></li>)}</ul>
      {results.length === 0 && <p className="navigation-command-empty" role="status">No matching destinations</p>}
    </dialog>
  </>;
}
