import { DASH, money, multiple, pct, toNum, yearOf } from "@/lib/format";
import type { Analysis, Decimalish, IndicatorKey } from "@/lib/types";

type Column = {
  key: IndicatorKey;
  label: string;
  format: (v: Decimalish) => string;
};

/**
 * The headline figures of every closed exercise, one row per year.
 *
 * Replaces the card strip the *Trajetória* section used to end with. That strip
 * was already trying to be a table — four label/value pairs repeated per year —
 * but laid out as cards it forced a horizontal scroll and put the years side by
 * side while the reader wants to run *down* a column and see a trend. The
 * reference platform settled on a table for the same reason.
 *
 * The columns are statement figures plus the two ratios a reader scans a history
 * for. Everything else lives in the indicator grid, one click from its drill-down.
 */
const COLUMNS: Column[] = [
  { key: "revenue", label: "Receita", format: money },
  { key: "net_income", label: "Lucro líq.", format: money },
  { key: "net_margin", label: "Margem líq.", format: pct },
  { key: "net_debt", label: "Dív. líquida", format: money },
  { key: "net_debt_to_ebitda", label: "Dív.líq./EBITDA", format: multiple },
  { key: "roe", label: "ROE", format: pct },
  { key: "pe", label: "P/L", format: multiple },
  { key: "dividend_yield", label: "DY", format: pct },
];

export function HistoryTable({ history }: { history: Analysis[] }) {
  // A column every exercise reports as inapplicable to the filer's regime is not
  // rendered at all (the #33 rule): a bank's leverage columns would be a wall of
  // n/d saying nothing. The API states the cause; nothing is inferred here.
  const columns = COLUMNS.filter(
    (c) =>
      !history.every(
        (h) => h.indicators.null_reasons[c.key] === "inapplicable_regime",
      ),
  );
  // Newest first: the year a reader wants is the last one, and on a phone it
  // should not sit behind six rows of scrolling.
  const rows = [...history].reverse();

  return (
    <div className="panel overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-900/80">
            <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-ink-500">
              Ano
            </th>
            {columns.map((c) => (
              <th
                key={c.key}
                className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-ink-500"
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr
              key={h.reference_date}
              className="border-b border-ink-900/40 last:border-0 transition-colors hover:bg-vault-900/40"
            >
              <td className="nums px-4 py-3 text-left font-display text-base text-ink-100">
                {yearOf(h.reference_date)}
              </td>
              {columns.map((c) => {
                const text = c.format(h.indicators[c.key]);
                return (
                  <td
                    key={c.key}
                    className={`nums px-4 py-3 text-right ${
                      toNum(h.indicators[c.key]) === null ? "text-ink-600" : "text-ink-100"
                    }`}
                  >
                    {text === DASH ? DASH : text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
