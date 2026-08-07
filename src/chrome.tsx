// Shared page furniture for the full-screen pages: one visual language
// for the store, mod detail and collection detail instead of three
// hand-rolled variants. QAM panels deliberately stay native Steam.
import { CSSProperties, ReactNode } from "react";

import { NEXUS_ORANGE } from "./theme";

/** Faded artwork banner behind a page header - depth without cost. It
 * scrolls away with the content (absolute inside the scroller), so it
 * never competes with the page once the user is reading. */
export function PageBackdrop({
  src,
  height = 230,
  blur = true,
}: {
  src?: string;
  height?: number;
  /** Game art reads fine sharp; mod/collection art is arbitrary imagery,
   * so it gets blurred into pure atmosphere. */
  blur?: boolean;
}) {
  if (!src) return null;
  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        height: `${height}px`,
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 0,
      }}
    >
      <img
        src={src}
        alt=""
        onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          opacity: blur ? 0.22 : 0.28,
          ...(blur ? { filter: "blur(20px)", transform: "scale(1.15)" } : {}),
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(180deg, rgba(11,14,19,0.15) 0%, rgba(11,14,19,0.55) 55%, rgba(11,14,19,1) 100%)",
        }}
      />
    </div>
  );
}

/** Section heading with the brand accent bar - the 10-foot-UI signpost. */
export function SectionHeading({
  title,
  right,
}: {
  title: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        margin: "18px 0 8px",
      }}
    >
      <div
        style={{
          width: "4px",
          height: "18px",
          background: NEXUS_ORANGE,
          borderRadius: "2px",
        }}
      />
      <h3 style={{ margin: 0 }}>{title}</h3>
      {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
    </div>
  );
}

/** Small stat pill (endorsements, downloads, mod count, size) for the
 * detail-page headers - scannable facts instead of a dot-separated
 * sentence. */
export function StatChip({
  icon,
  children,
}: {
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "3px 11px",
        borderRadius: "999px",
        background: "rgba(255,255,255,0.08)",
        border: "1px solid rgba(255,255,255,0.10)",
        fontSize: "12.5px",
        whiteSpace: "nowrap",
      }}
    >
      {icon && <span style={{ opacity: 0.75, display: "inline-flex" }}>{icon}</span>}
      {children}
    </span>
  );
}

/** Collection thumbnail with card layers fanned out behind it - the
 * one-glance signal that this tile is a STACK of mods, not one mod.
 * `peek` is how far each layer shows beyond the previous one. */
export function StackedThumb({
  src,
  width,
  height,
  peek = 6,
  radius = 4,
}: {
  src?: string;
  width: number;
  height: number;
  peek?: number;
  radius?: number;
}) {
  const layer = (offset: number, opacity: number): CSSProperties => ({
    position: "absolute",
    // Each card behind is vertically inset a little more, like a deck
    // squared up at the left edge and fanned to the right.
    top: `${offset}px`,
    bottom: `${offset}px`,
    left: `${offset * 2}px`,
    width: `${width}px`,
    background: `rgba(255,255,255,${opacity})`,
    borderRadius: `${radius}px`,
  });
  return (
    <div
      style={{
        position: "relative",
        width: `${width + peek * 2}px`,
        height: `${height}px`,
        flexShrink: 0,
      }}
    >
      <div style={layer(peek, 0.1)} />
      <div style={layer(peek / 2, 0.22)} />
      {src ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: `${width}px`,
            height: "100%",
            objectFit: "cover",
            borderRadius: `${radius}px`,
            display: "block",
          }}
        />
      ) : (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: `${width}px`,
            height: "100%",
            background: "#23262e",
            borderRadius: `${radius}px`,
          }}
        />
      )}
    </div>
  );
}
