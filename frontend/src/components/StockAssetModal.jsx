import { useState, useEffect, useMemo } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { X, Search } from "lucide-react";

const sourceLabel = { pexels: "PEXELS", pixabay: "PIXABAY", unsplash: "UNSPLASH", mixed: "MIXED" };
const sourceColor = { pexels: "bg-[#05A081]/20 text-[#05A081]", pixabay: "bg-[#00ABE7]/20 text-[#00ABE7]", unsplash: "bg-white/10 text-white", mixed: "bg-[#00FF88]/20 text-[#00FF88]" };

export default function StockAssetModal({ open, onOpenChange, scene, onAttach, projectAssets = [], searchStock, results = [], source = "mixed", loading }) {
  const [query, setQuery] = useState("");
  const [mediaType, setMediaType] = useState("all");
  const [attached, setAttached] = useState(new Set());

  // FIXED: Use search_terms[0] - keyword optimized, not narrative visual
  const buildQuery = (s) => {
    if (!s) return "ancient Rome";
    const terms = s.search_terms || [];
    if (terms.length > 0 && terms[0].trim().length > 2) {
      return terms[0].trim().slice(0, 60);
    }
    const visual = (s.visual_direction || s.visual || "").trim();
    return visual.split(/[,.—\n]/)[0].trim().slice(0, 60) || "ancient Rome";
  };

  useEffect(() => {
    if (open && scene) {
      const existing = new Set((projectAssets || []).map(a => a.external_id || a.preview_url));
      setAttached(existing);
      const q = buildQuery(scene);
      setQuery(q);
      searchStock?.({ query: q, media_type: mediaType, per_page: 24, exclude_ids: Array.from(existing) });
    }
  }, [open, scene?.id]);

  const handleSearch = (e) => {
    e?.preventDefault();
    const q = query.trim().slice(0, 60);
    searchStock?.({ query: q, media_type: mediaType, per_page: 24, exclude_ids: Array.from(attached) });
  };

  const dedupedResults = useMemo(() => {
    const seen = new Set();
    return (results || []).filter(r => {
      const key = r.preview_url || r.external_id || r.id;
      if (seen.has(key) || attached.has(r.external_id)) return false;
      seen.add(key);
      return true;
    });
  }, [results, attached]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[96vw] max-w-5xl max-h-[96vh] sm:max-h-[90vh] m-0 sm:m-auto overflow-hidden p-0 flex flex-col [&>button:last-child]:hidden bg-[#0A0A0A] border-[#1A1A1A]">
        <button onClick={() => onOpenChange(false)} className="absolute right-2 top-2 sm:right-4 sm:top-4 p-3 bg-black/80 rounded-full sm:rounded-sm backdrop-blur z-20 hover:bg-black">
          <X size={20} className="sm:w-[18px] text-white" />
        </button>
        <DialogHeader className="p-4 sm:p-6 pb-0 shrink-0">
          <div className="flex items-center gap-3">
            <DialogTitle className="text-white">Find Stock Assets</DialogTitle>
            <span className={`text-[10px] px-2 py-1 border ${sourceColor[source] || sourceColor.mixed}`}>{sourceLabel[source] || "MIXED"} · LIVE</span>
          </div>
          <div className="text-[10px] text-white/50 tracking-[0.2em] mt-1">SCENE {scene?.index || "02"} · ATTACH SUGGESTIONS</div>
        </DialogHeader>
        <form onSubmit={handleSearch} className="p-4 sm:p-6 pt-3 flex flex-col sm:flex-row items-stretch sm:items-center gap-2 shrink-0">
          <div className="relative flex-1 w-full">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
            <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search Pexels, Pixabay, Unsplash…" className="pl-9 py-3 sm:py-2 bg-[#141414] border-[#222] text-white w-full" />
          </div>
          <div className="flex gap-2 w-full sm:w-auto">
            <div className="flex flex-1 sm:flex-none bg-[#141414] border border-[#222] rounded">
              {["all","videos","photos"].map(t => (
                <button key={t} type="button" onClick={()=>{setMediaType(t); searchStock?.({query: query.slice(0,60), media_type:t, per_page:24})}} className={`flex-1 sm:flex-none min-h-[44px] sm:min-h-0 px-3 py-3 sm:py-2 text-[11px] tracking-widest ${mediaType===t?"bg-cyan-400 text-black":"text-white/60 hover:text-white active:bg-[#1A1A1A]"}`}>{t.toUpperCase()}</button>
              ))}
            </div>
            <Button type="submit" className="min-h-[44px] min-w-[64px] px-5 py-3 sm:py-2 bg-cyan-400 text-black hover:bg-cyan-300"><Search className="w-4 h-4 sm:mr-2" /><span className="hidden sm:inline">Search</span></Button>
          </div>
        </form>
        <div className="px-4 sm:px-6 text-[10px] text-white/40 tracking-widest">QUERY · <span className="text-cyan-400">{query}</span></div>
        <div className="flex-1 overflow-y-auto p-3 sm:p-6 overscroll-contain touch-pan-y" style={{WebkitOverflowScrolling:'touch'}}>
          {loading? <div className="min-h-[40vh] flex items-center justify-center text-white/40">Searching {sourceLabel[source]}…</div> :
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-3 gap-3">
              {dedupedResults.map(r => (
                <div key={r.external_id || r.id} className="group bg-[#141414] border border-[#1E1E1E] overflow-hidden hover:border-[#2A2A2A] transition">
                  <div className="relative aspect-[16/9] sm:aspect-video overflow-hidden bg-black">
                    {r.preview_url? <img src={r.preview_url} className="w-full h-full object-cover" loading="lazy" /> : <div className="w-full h-full bg-[#111]" />}
                    <span className="absolute top-2 left-2 text-[9px] px-1.5 py-0.5 bg-black/60 backdrop-blur border border-white/10 text-cyan-300">PHOTO</span>
                    <span className="absolute top-2 right-2 text-[9px] px-1.5 py-0.5 bg-black/70 text-white/80">{(r.source||source).toUpperCase()}</span>
                    {attached.has(r.external_id) && <span className="absolute bottom-2 left-2 text-[8px] px-1.5 py-0.5 bg-amber-500/90 text-black">USED</span>}
                  </div>
                  <div className="p-3">
                    <div className="text-[13px] text-white/90 line-clamp-2 min-h-[36px]">{r.title || r.description || "Untitled"}</div>
                    <div className="text-[10px] text-white/40 mt-1 truncate">BY { (r.photographer||r.author||"UNKNOWN").toUpperCase()} · {r.width}×{r.height}</div>
                    <Button onClick={()=>{setAttached(prev=>new Set([...prev, r.external_id])); onAttach?.(r)}} disabled={attached.has(r.external_id)} className="w-full mt-3 min-h-[40px] py-2.5 sm:py-1.5 bg-cyan-400 text-black hover:bg-cyan-300 disabled:opacity-30 active:scale-[0.98] transition-transform text-[11px] tracking-widest">{attached.has(r.external_id)?"ATTACHED":"ATTACH"}</Button>
                  </div>
                </div>
              ))}
            </div>
          }
        </div>
        <div className="shrink-0 bg-[#0A0A0A] px-4 sm:px-6 py-3 flex justify-between items-center border-t border-[#1A1A1A]" style={{paddingBottom:'max(0.75rem, env(safe-area-inset-bottom))'}}>
          <div className="text-[10px] text-white/30 tracking-widest">SOURCE · {source.toUpperCase()}</div>
          <Button variant="ghost" onClick={()=>onOpenChange(false)} className="min-h-[44px] px-4 text-white/60">Done</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
