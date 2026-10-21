import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { SECTIONS_STORAGE_KEY } from "../sections";

function stubMatchMedia(matches: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches, media: q, addEventListener() {}, removeEventListener() {} }));
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 200 }))));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("sidebar collapse", () => {
  it("is expanded by default on desktop and toggles", () => {
    stubMatchMedia(false);
    render(<App />);
    const toggle = screen.getByTestId("sidebar-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector(".sidebar")).not.toHaveClass("sidebar-collapsed");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".sidebar")).toHaveClass("sidebar-collapsed");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("defaults to collapsed on mobile widths", () => {
    stubMatchMedia(true);
    render(<App />);
    expect(screen.getByTestId("sidebar-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".sidebar")).toHaveClass("sidebar-collapsed");
  });

  it("persists the choice and it overrides the viewport default", () => {
    stubMatchMedia(false);
    const first = render(<App />);
    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(JSON.parse(window.localStorage.getItem(SECTIONS_STORAGE_KEY)!)).toMatchObject({ sidebar: false });
    first.unmount();
    render(<App />);
    expect(screen.getByTestId("sidebar-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("falls back to expanded when matchMedia is unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<App />);
    expect(screen.getByTestId("sidebar-toggle")).toHaveAttribute("aria-expanded", "true");
  });
});
