import React, { useEffect, useState } from "react";
import { Activity, Clock } from "lucide-react";
import { api } from "../lib/api";

export default function TopBar({ title, subtitle, right }) {
  const [now, setNow] = useState(new Date());
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        await api.get("/health");
        if (alive) setOnline(true);
      } catch {
        if (alive) setOnline(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <header
      data-testid="topbar"
      className="sticky top-0 z-30 h-16 border-b border-[rgba(59,130,246,0.15)] bg-[#0f141f]/90 backdrop-blur-xl flex items-center justify-between px-8 pl-14 md:pl-8"
    >
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {subtitle && (
            <span className="font-mono text-[10px] text-zinc-500 uppercase tracking-widest">
              / {subtitle}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-4">
        {right}
        <div className="hidden md:flex items-center gap-2 font-mono text-[11px] text-zinc-500">
          <Activity size={12} strokeWidth={1.5} style={{ color: online ? "#00FF66" : "#FF3366" }} />
          <span>{online ? "SYSTEM · ONLINE" : "SYSTEM · DEGRADED"}</span>
        </div>
        <div className="hidden md:flex items-center gap-2 font-mono text-[11px] text-zinc-500">
          <Clock size={12} strokeWidth={1.5} />
          <span>{now.toUTCString().split(" ").slice(4, 5)[0]} UTC</span>
        </div>
      </div>
    </header>
  );
}
