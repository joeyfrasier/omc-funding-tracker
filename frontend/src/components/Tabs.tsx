"use client";

import { useRef, useEffect } from "react";

interface TabsProps {
  tabs: string[];
  active: number;
  onChange: (index: number) => void;
}

export default function Tabs({ tabs, active, onChange }: TabsProps) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow key navigation between tabs
  const handleKeyDown = (e: React.KeyboardEvent, index: number) => {
    let next = index;
    if (e.key === "ArrowRight" || e.key === "Tab" && !e.shiftKey) {
      e.preventDefault();
      next = (index + 1) % tabs.length;
    } else if (e.key === "ArrowLeft" || e.key === "Tab" && e.shiftKey) {
      e.preventDefault();
      next = (index - 1 + tabs.length) % tabs.length;
    } else {
      return;
    }
    onChange(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <nav className="tab-nav" role="tablist">
      {tabs.map((tab, i) => (
        <button
          key={tab}
          ref={(el) => { tabRefs.current[i] = el; }}
          role="tab"
          aria-selected={i === active}
          tabIndex={i === active ? 0 : -1}
          className={`tab-item ${i === active ? "active" : ""}`}
          onClick={() => onChange(i)}
          onKeyDown={(e) => handleKeyDown(e, i)}
        >
          {tab}
        </button>
      ))}
    </nav>
  );
}
