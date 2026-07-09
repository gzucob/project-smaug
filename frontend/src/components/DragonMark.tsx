import type { CSSProperties } from "react";
import { GiSpikedDragonHead } from "react-icons/gi";

/**
 * Smaug — the brand mark: a horned dragon head (Game Icons, via react-icons),
 * filled with the molten-gold gradient and lit by an ember glow.
 *
 * The gradient is defined once per instance in a 0×0 SVG and referenced by the
 * icon's `fill`; `color` is a solid-gold fallback if the gradient can't resolve.
 */
type DragonMarkProps = {
  size?: number;
  className?: string;
  withFlame?: boolean; // stronger ember glow (hero / empty states)
  animated?: boolean; // gentle ember pulse
};

export function DragonMark({
  size = 40,
  className,
  withFlame = false,
  animated = false,
}: DragonMarkProps) {
  const gid = `smaug-gold-${size}-${withFlame ? "f" : "n"}`;
  const glow = withFlame
    ? "drop-shadow(0 0 12px color-mix(in oklab, var(--color-ember-500) 65%, transparent)) drop-shadow(0 0 4px var(--color-gold-400))"
    : "drop-shadow(0 0 5px color-mix(in oklab, var(--color-ember-500) 40%, transparent))";

  return (
    <span className={`inline-flex ${className ?? ""}`} style={{ lineHeight: 0 }}>
      <svg width="0" height="0" aria-hidden focusable="false" style={{ position: "absolute" }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--color-gold-300)" />
            <stop offset="50%" stopColor="var(--color-gold-500)" />
            <stop offset="100%" stopColor="var(--color-ember-600)" />
          </linearGradient>
        </defs>
      </svg>
      <GiSpikedDragonHead
        size={size}
        role="img"
        aria-label="Smaug"
        fill={`url(#${gid})`}
        className={animated ? "ember-pulse" : undefined}
        style={{ color: "var(--color-gold-400)", filter: glow } as CSSProperties}
      />
    </span>
  );
}
