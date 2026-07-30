"use client";

/**
 * The drill-down's title, doubling as an indicator picker.
 *
 * This was a native `<select>` sitting invisible over the heading — free
 * keyboard and touch behaviour, at a price that only shows once you open it:
 * the dropdown is drawn by the operating system, so its type, row height and
 * group headings are the platform's and unreachable from CSS (#143). Here the
 * list is ours; what the native element gave for free is re-implemented
 * deliberately, and listed in the issue.
 */
import { useEffect, useId, useRef, useState } from "react";
import { FiChevronDown } from "react-icons/fi";
import { INDICATOR_GROUPS, groupColor, specsByGroup } from "@/lib/indicators";
import type { IndicatorKey } from "@/lib/types";

const OPTIONS = INDICATOR_GROUPS.flatMap((group) =>
  specsByGroup(group).map((spec) => ({ group, key: spec.key, label: spec.label })),
);

export function IndicatorPicker({
  value,
  label,
  onChange,
  onOpenChange,
}: {
  /** The grid indicator in focus — a `_total` column shows its sibling (#121). */
  value: IndicatorKey;
  label: string;
  onChange: (key: IndicatorKey) => void;
  /** Lets the modal yield ArrowLeft/Right to the list while it is open. */
  onOpenChange: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(() => Math.max(0, OPTIONS.findIndex((o) => o.key === value)));
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const close = (returnFocus = true) => {
    setOpen(false);
    onOpenChange(false);
    if (returnFocus) rootRef.current?.querySelector("button")?.focus();
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    onOpenChange(next);
    if (next) setActive(Math.max(0, OPTIONS.findIndex((o) => o.key === value)));
  };

  // Outside click and scroll dismiss it, as a native dropdown would.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep the highlighted row in view while arrowing through 29 of them.
  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
        e.preventDefault();
        toggle();
      }
      return;
    }
    // While the list is open these keys are the list's, not the modal's.
    e.stopPropagation();
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => (i + 1) % OPTIONS.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => (i - 1 + OPTIONS.length) % OPTIONS.length);
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(OPTIONS.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        onChange(OPTIONS[active].key);
        close();
        break;
      case "Escape":
        e.preventDefault();
        close();
        break;
      case "Tab":
        close(false);
        break;
    }
  };

  return (
    <div ref={rootRef} className="relative" onKeyDown={onKeyDown}>
      <button
        type="button"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label="Trocar de indicador"
        className="group/pick flex items-center gap-1.5 rounded-md focus-visible:outline-1 focus-visible:outline-gold-500"
      >
        <span className="font-display text-2xl text-ink-50 transition-colors group-hover/pick:text-gold-300">
          {label}
        </span>
        <FiChevronDown
          size={15}
          className={`text-ink-500 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label="Indicadores"
          tabIndex={-1}
          className="panel absolute left-0 top-full z-20 mt-2 max-h-[19rem] w-60 overflow-y-auto py-1 shadow-xl"
        >
          {INDICATOR_GROUPS.map((group) => {
            const specs = specsByGroup(group);
            if (specs.length === 0) return null;
            return (
              <div key={group}>
                <div
                  className="px-3 pb-1 pt-2 text-[0.6rem] font-semibold uppercase tracking-[0.16em]"
                  style={{ color: groupColor(group) }}
                >
                  {group}
                </div>
                {specs.map((spec) => {
                  const index = OPTIONS.findIndex((o) => o.key === spec.key);
                  const selected = spec.key === value;
                  const highlighted = index === active;
                  return (
                    <div
                      key={spec.key}
                      data-index={index}
                      role="option"
                      aria-selected={selected}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => {
                        onChange(spec.key);
                        close();
                      }}
                      className="cursor-pointer px-3 py-1.5 text-sm transition-colors"
                      style={{
                        backgroundColor: highlighted ? "var(--color-vault-800)" : "transparent",
                        color: selected ? groupColor(group) : "var(--color-ink-200)",
                      }}
                    >
                      {spec.label}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
