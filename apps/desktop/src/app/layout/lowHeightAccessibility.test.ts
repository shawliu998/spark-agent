import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const desktopCss = readFileSync("src/index.css", "utf8");

describe("low-height desktop accessibility", () => {
  it("keeps global navigation rows at least 44px tall", () => {
    const start = desktopCss.indexOf("@media (max-height: 760px)");
    const end = desktopCss.indexOf("@media (prefers-reduced-motion: reduce)", start);
    const lowHeightRules = desktopCss.slice(start, end);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(lowHeightRules).toContain(".global-sidebar-row");
    expect(lowHeightRules).toContain("min-height: 44px");
    expect(lowHeightRules).not.toContain("min-height: 40px");
  });

  it("keeps the global reduced-motion boundary intact", () => {
    const start = desktopCss.indexOf("@media (prefers-reduced-motion: reduce)");
    const end = desktopCss.indexOf("/* Literature workspace", start);
    const reducedMotionRules = desktopCss.slice(start, end);

    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    expect(reducedMotionRules).toContain("scroll-behavior: auto !important");
    expect(reducedMotionRules).toContain("animation-duration: 0.01ms !important");
    expect(reducedMotionRules).toContain("animation-iteration-count: 1 !important");
    expect(reducedMotionRules).toContain("transition-duration: 0.01ms !important");
  });
});
