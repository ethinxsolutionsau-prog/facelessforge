import React, { useState } from "react";
import Sidebar from "./Sidebar";
import { Menu, X } from "lucide-react";

export default function AppShell({ children }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen bg-[#0f141f] text-slate-200">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      {/* overlay for mobile when open */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      )}
      {/* hamburger button - visible on mobile only */}
      <button
        data-testid="sidebar-toggle"
        onClick={() => setOpen(!open)}
        className="fixed top-3 left-3 z-40 md:hidden bg-[#1c2333] border border-[rgba(59,130,246,0.15)] p-2 rounded-md text-slate-200"
        aria-label="Toggle sidebar"
      >
        {open ? <X size={18} /> : <Menu size={18} />}
      </button>
      <div className={`min-h-screen flex flex-col transition-all duration-200 ${open ? "ml-0 md:ml-60" : "ml-0 md:ml-60"} w-full md:w-auto`}>
        {/* when sidebar closed on mobile, main takes w-full; sidebar uses translateX(-100%) so no overlap */}
        <div className="md:ml-0 ml-0 w-full">
          {children}
        </div>
      </div>
    </div>
  );
}
