import { useMemo, useRef, useState } from "react";
import type { ModelOption } from "../types";

/**
 * A searchable model picker that still accepts a typed model ID verbatim
 * (advanced users can paste any ID). Selection is by id; the visible text is
 * the friendly name when it matches a known model.
 */
export default function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Search models…",
}: {
  value: string;
  onChange: (id: string) => void;
  options: ModelOption[];
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const blurTimer = useRef<number | null>(null);

  const matched = options.find((o) => o.id === value);
  const text = open ? query : matched ? matched.name : value;

  const filtered = useMemo(() => {
    const term = (open ? query : matched ? matched.name : value).trim().toLowerCase();
    const list = options.filter(
      (o) => o.name.toLowerCase().includes(term) || o.id.toLowerCase().includes(term),
    );
    return list.slice(0, 80);
  }, [options, query, open, value, matched]);

  const pick = (id: string) => {
    onChange(id);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="combo">
      <input
        className="input"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        placeholder={placeholder}
        value={text}
        onFocus={() => {
          if (blurTimer.current) window.clearTimeout(blurTimer.current);
          setOpen(true);
        }}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => {
            setOpen(false);
            setQuery("");
          }, 120);
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          if (!open) setOpen(true);
        }}
      />
      {open ? (
        <ul className="combo-menu" onMouseDown={(e) => e.preventDefault()}>
          {filtered.length === 0 ? (
            <li className="combo-empty">
              Use <strong>{query || value || "model-id"}</strong>
              <button
                className="combo-use"
                onClick={() => query && pick(query)}
                type="button"
              >
                Use this
              </button>
            </li>
          ) : (
            filtered.map((o) => (
              <li
                key={o.id}
                className="combo-opt"
                data-selected={o.id === value}
                onClick={() => pick(o.id)}
              >
                <span className="combo-name">{o.name}</span>
                <span className="combo-id">{o.id}</span>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}
