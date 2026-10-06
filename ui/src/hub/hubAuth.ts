/** Build entry for the hub login / index page script (emitted as `hub-auth.js` + `hub-auth.css`). */
import "./hub-auth.css";
import { initHubAuth } from "./hubAuthCore";

// Exports nothing, so the IIFE bundle needs no global name.
const start = (): void => initHubAuth(document, window);
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
