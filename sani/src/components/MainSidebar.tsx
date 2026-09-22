export type MainSection = "home" | "conversations" | "agents" | "voice" | "control" | "layout" | "settings" | "diagnostics";
const sections: Array<[MainSection, string]> = [["home","Home"],["conversations","Conversations"],["agents","Agents"],["voice","Voice"],["control","Computer Control"],["layout","Layout"],["settings","Settings"],["diagnostics","Diagnostics"]];
export default function MainSidebar({ selected, onSelect }: { selected: MainSection; onSelect: (section: MainSection) => void }) {
  return <nav className="main-sidebar" aria-label="Sani navigation"><div className="brand">Sani</div><div className="nav-items">{sections.map(([id,label]) => <button key={id} className={selected === id ? "nav-item selected" : "nav-item"} aria-current={selected === id ? "page" : undefined} onClick={() => onSelect(id)}><span className="nav-mark" aria-hidden="true">•</span><span>{label}</span></button>)}</div><div className="sidebar-footer">Sani desktop</div></nav>;
}
