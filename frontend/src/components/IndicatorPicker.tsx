"use client";

/**
 * The drill-down's title, doubling as an indicator picker.
 *
 * Built on Headless UI's `Listbox` rather than hand-rolled. It was a native
 * `<select>` first — the dropdown is drawn by the operating system, unreachable
 * from CSS (#143) — then ours, which worked but re-implemented keyboard
 * handling and anchored the list *inside* a scrolling column, where a longer
 * list or a shorter panel would clip it. `anchor` renders the list in a portal
 * and flips it when it does not fit; type-ahead ("mar" jumps to Margem líquida)
 * comes with it.
 *
 * Styling stays ours: the panel surface, the hairline, the group pastels.
 */
import { Listbox, ListboxButton, ListboxOption, ListboxOptions } from "@headlessui/react";
import { FiChevronDown } from "react-icons/fi";
import { INDICATOR_GROUPS, groupColor, specsByGroup } from "@/lib/indicators";
import type { IndicatorKey } from "@/lib/types";

export function IndicatorPicker({
  value,
  label,
  onChange,
}: {
  /** The grid indicator in focus — a `_total` column shows its sibling (#121). */
  value: IndicatorKey;
  label: string;
  onChange: (key: IndicatorKey) => void;
}) {
  return (
    <Listbox value={value} onChange={onChange}>
      <ListboxButton
        aria-label="Trocar de indicador"
        className="group/pick flex items-center gap-1.5 rounded-md focus-visible:outline-1 focus-visible:outline-gold-500"
      >
        <span className="font-display text-2xl text-ink-50 transition-colors group-hover/pick:text-gold-300">
          {label}
        </span>
        <FiChevronDown size={15} className="text-ink-500 transition-transform group-data-open/pick:rotate-180" />
      </ListboxButton>

      {/* `--anchor-max-height` is what bounds the list: `anchor` measures the
          space available and writes the height inline, which beats a class.
          Left to itself it fills the viewport and buries the panel behind it. */}
      <ListboxOptions
        anchor="bottom start"
        className="panel z-50 w-60 overflow-y-auto py-1 shadow-xl focus:outline-none [--anchor-gap:0.5rem] [--anchor-max-height:19rem]"
      >
        {INDICATOR_GROUPS.map((group) => (
          <div key={group}>
            <div
              className="px-3 pb-1 pt-2 text-[0.6rem] font-semibold uppercase tracking-[0.16em]"
              style={{ color: groupColor(group) }}
            >
              {group}
            </div>
            {specsByGroup(group).map((spec) => (
              <ListboxOption
                key={spec.key}
                value={spec.key}
                className="cursor-pointer px-3 py-1.5 text-sm text-ink-200 transition-colors data-focus:bg-vault-800"
              >
                {({ selected }) => (
                  <span style={selected ? { color: groupColor(group) } : undefined}>
                    {spec.label}
                  </span>
                )}
              </ListboxOption>
            ))}
          </div>
        ))}
      </ListboxOptions>
    </Listbox>
  );
}
