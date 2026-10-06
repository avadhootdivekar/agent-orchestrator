/// <reference types="vite/client" />
/** Shared builders for the hub-page vitest files (`hub-auth.test.ts`, `auth-client-contract.test.ts`). */
import { vi } from "vitest";
import indexHtml from "./hub-index.html?raw";
import loginHtml from "./hub-login.html?raw";

/**
 * A plain-object `Response` stand-in. Everything it does resolves in microtasks, so tests that
 * run under fake timers never wait on a real stream.
 */
export function fakeResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: { get: (name: string) => lower[name.toLowerCase()] ?? null },
    json: async () => body,
  } as unknown as Response;
}

/** A fresh document holding the body of `html`. Fresh per test, so listeners never leak across tests. */
function documentFrom(html: string): Document {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const doc = document.implementation.createHTMLDocument(parsed.title);
  doc.body.innerHTML = parsed.body.innerHTML;
  Object.defineProperty(doc, "visibilityState", { value: "visible", configurable: true });
  return doc;
}

export const mountLogin = (): Document => documentFrom(loginHtml);
export const mountIndex = (): Document => documentFrom(indexHtml);

export function setVisibility(doc: Document, state: "visible" | "hidden"): void {
  Object.defineProperty(doc, "visibilityState", { value: state, configurable: true });
}

export interface FakeWin {
  win: Window;
  replace: ReturnType<typeof vi.fn>;
  writeText: ReturnType<typeof vi.fn>;
}

/** A `Window` stand-in: real `localStorage`, a spy `location.replace`, a spy clipboard. */
export function makeWin(options: { clipboard?: boolean } = {}): FakeWin {
  const replace = vi.fn();
  const writeText = vi.fn(async () => undefined);
  const navigator = options.clipboard === false ? {} : { clipboard: { writeText } };
  const win = { location: { replace }, localStorage: window.localStorage, navigator } as unknown as Window;
  return { win, replace, writeText };
}

/** Let pending promise continuations (not timers) run. */
export async function settle(): Promise<void> {
  for (let i = 0; i < 25; i++) await Promise.resolve();
}

export function byId<T extends HTMLElement = HTMLElement>(doc: Document, id: string): T {
  const found = doc.getElementById(id);
  if (!found) throw new Error(`no element #${id}`);
  return found as T;
}

export function typeInto(doc: Document, id: string, value: string): void {
  byId<HTMLInputElement>(doc, id).value = value;
}

/** Dispatch a cancelable `submit`; returns the event so callers can assert `defaultPrevented`. */
export function submit(doc: Document, formId: string): Event {
  const event = new Event("submit", { cancelable: true, bubbles: true });
  byId(doc, formId).dispatchEvent(event);
  return event;
}

export function click(doc: Document, id: string): void {
  byId(doc, id).dispatchEvent(new Event("click", { cancelable: true, bubbles: true }));
}

export const isShown = (doc: Document, id: string): boolean => !byId(doc, id).hidden;

/** The ids of the five login-page blocks, for "exactly this step is visible" assertions. */
export type LoginStep = "login" | "totp" | "enroll_token" | "enroll_confirm" | "codes";

/** Which step the login page is currently showing (throws if it is ambiguous). */
export function visibleStep(doc: Document): LoginStep {
  const login = isShown(doc, "ao-login-form");
  const totp = isShown(doc, "ao-totp-form");
  const token = isShown(doc, "ao-enroll") && isShown(doc, "ao-enroll-token-form");
  const confirm = isShown(doc, "ao-enroll") && isShown(doc, "ao-enroll-form");
  const codes = isShown(doc, "ao-recovery-codes");
  const shown = (
    [
      [login, "login"],
      [totp, "totp"],
      [token, "enroll_token"],
      [confirm, "enroll_confirm"],
      [codes, "codes"],
    ] as const
  ).filter(([on]) => on);
  if (shown.length !== 1) throw new Error(`expected one visible step, got ${shown.map(([, n]) => n)}`);
  return shown[0][1];
}
