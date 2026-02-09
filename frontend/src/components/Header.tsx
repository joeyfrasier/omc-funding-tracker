"use client";

import { useState, useRef, useEffect } from "react";

interface HeaderProps {
  onSearch?: (query: string) => void;
  onOpenSettings?: () => void;
}

export default function Header({ onSearch, onOpenSettings }: HeaderProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

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
        setMenuOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

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

          {/* Avatar menu */}
          <div className="relative" ref={menuRef}>
            <button
              className="w-9 h-9 rounded-full bg-[var(--color-ws-orange)] text-white flex items-center justify-center text-sm font-bold hover:opacity-90 transition-opacity"
              onClick={() => setMenuOpen(!menuOpen)}
              title="Settings"
            >
              JF
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full mt-2 w-48 bg-white rounded-xl shadow-lg border border-[var(--color-ws-gray)] py-1 z-50">
                <button
                  className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 flex items-center gap-3 transition-colors"
                  onClick={() => {
                    setMenuOpen(false);
                    onOpenSettings?.();
                  }}
                >
                  <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  Configuration
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
