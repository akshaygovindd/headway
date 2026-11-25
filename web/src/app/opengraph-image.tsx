import { ImageResponse } from "next/og";

// Rendered once at build time by Next itself: no external service, no runtime cost.
export const alt = "Headway: MBTA subway reliability";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: 80,
          background: "#0b1f33",
          color: "#ffffff",
        }}
      >
        <div style={{ fontSize: 96, fontWeight: 700 }}>Headway</div>
        <div style={{ fontSize: 40, marginTop: 24, color: "#cfe3f7" }}>MBTA subway reliability, measured in the open</div>
        <div style={{ fontSize: 28, marginTop: 40, color: "#9bb8d3" }}>
          Every number shown with its coverage and freshness
        </div>
      </div>
    ),
    size,
  );
}
