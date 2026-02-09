"use client";

import { useState, useRef, useEffect } from "react";

interface HeaderProps {
  onSearch?: (query: string) => void;
}

export default function Header({ onSearch }: HeaderProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const now = new Date();
  const formatted = now.toLocaleDateString("en-US", {
    month: "short", day: "2-digit", year: "numeric",
  }) + " · " + now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });

  useEffect(() => {
    if (searchOpen && inputRef.current) inputRef.current.focus();
  }, [searchOpen]);

  // Global keyboard shortcut: Cmd+K or Ctrl+K
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
      if (e.key === "Escape") {
        setSearchOpen(false);
        setQuery("");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim() && onSearch) onSearch(query.trim());
  };

  return (
    <header className="border-b-2 border-[var(--color-ws-gray)] pb-4 mb-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-2xl font-[900] tracking-tight text-black" style={{ fontFamily: "'Archivo', sans-serif" }}>
            Worksuite
          </span>
          <div className="w-px h-6 bg-[var(--color-ws-gray)]" />
          <span className="text-lg font-bold text-black">OMC Funding Manager</span>
        </div>

        <div className="flex items-center gap-4">
          {searchOpen ? (
            <form onSubmit={handleSubmit} className="flex items-center">
              <div className="relative">
                <input
                  ref={inputRef}
                  type="text"
                  className="border border-[var(--color-ws-gray)] rounded-full px-4 py-2 text-sm w-72 focus:outline-none focus:border-[var(--color-ws-orange)] transition-colors"
                  placeholder="Search NVC codes, agencies, emails…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onBlur={() => { if (!query) setSearchOpen(false); }}
                />
                <kbd className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-gray-400 border border-gray-200 rounded px-1.5 py-0.5">
                  ESC
                </kbd>
              </div>
            </form>
          ) : (
            <button
              className="flex items-center gap-2 border border-[var(--color-ws-gray)] rounded-full px-4 py-2 text-sm text-gray-400 hover:border-gray-400 transition-colors"
              onClick={() => setSearchOpen(true)}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              Search
              <kbd className="text-[10px] border border-gray-200 rounded px-1.5 py-0.5 ml-2">⌘K</kbd>
            </button>
          )}
          <span className="text-xs text-gray-400">{formatted}</span>
        </div>
      </div>
    </header>
  );
}
