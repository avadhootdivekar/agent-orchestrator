import { useState } from "react";
import { authErrorMessage } from "./messages";
import { msgRecoveryUsed } from "./constants";
import { ChangePasswordDialog } from "./ChangePasswordDialog";
import { clearClientState } from "./clearClientState";
import { useAuth } from "./context";
import { DisableTotpDialog } from "./DisableTotpDialog";
import { EnableTotpDialog } from "./EnableTotpDialog";
import { RegenerateCodesDialog } from "./RegenerateCodesDialog";

type DialogKind = "password" | "enable" | "disable" | "regenerate";

/**
 * Sidebar-footer account menu (HLD §17.1). Renders only when auth is on and a user is signed in,
 * so with auth off the dashboard is byte-for-byte unchanged. Which two-factor actions appear is
 * decided by the server (`can_enroll_totp` / `can_disable_totp`) plus whether the user is enrolled.
 */
export function AccountMenu() {
  const { status, refresh, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [error, setError] = useState<string | null>(null);

  const user = status?.enabled ? status.user : null;
  if (!status || !user) return null;

  const close = () => setDialog(null);
  // A status refresh failing must not break the dialog flow: the next poll/401 reconciles it.
  const reload = () => void refresh().catch(() => undefined);

  async function signOut(everywhere: boolean) {
    setError(null);
    clearClientState();
    try {
      await logout(everywhere);
    } catch (err) {
      // Only "log out everywhere" can reject (other sessions may still be live): say so.
      setError(authErrorMessage(err));
    }
  }

  const pick = (kind: DialogKind) => {
    setOpen(false);
    setDialog(kind);
  };

  return (
    <div className="account-menu">
      <button
        type="button"
        className="nav-item"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden="true">◉</span>
        {user.username}
      </button>
      {open && (
        <div className="account-panel" role="group" aria-label="Account">
          <p className="account-meta">
            Signed in as <strong>{user.username}</strong>
            <br />
            {user.auth_method === "password+totp" ? "Password + authenticator" : "Password"}
          </p>
          <button type="button" className="nav-item" onClick={() => pick("password")}>
            Change password
          </button>
          {user.can_enroll_totp && (
            <button type="button" className="nav-item" onClick={() => pick("enable")}>
              Enable two-factor
            </button>
          )}
          {user.totp_enrolled && user.can_disable_totp && (
            <button type="button" className="nav-item" onClick={() => pick("disable")}>
              Disable two-factor
            </button>
          )}
          {user.totp_enrolled && (
            <button type="button" className="nav-item" onClick={() => pick("regenerate")}>
              Regenerate recovery codes
            </button>
          )}
          <button type="button" className="nav-item" onClick={() => void signOut(false)}>
            Log out
          </button>
          <button type="button" className="nav-item" onClick={() => void signOut(true)}>
            Log out everywhere
          </button>
        </div>
      )}
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {dialog === "password" && <ChangePasswordDialog policy={status.policy} onClose={close} />}
      {dialog === "enable" && <EnableTotpDialog onClose={close} onEnrolled={reload} />}
      {dialog === "disable" && <DisableTotpDialog onClose={close} onDisabled={reload} />}
      {dialog === "regenerate" && <RegenerateCodesDialog onClose={close} onRegenerated={reload} />}
    </div>
  );
}

/** One-shot banner after a recovery-code sign-in (§17.8); dismissible, rendered at the top of the main area. */
export function RecoveryBanner() {
  const { recoveryNotice } = useAuth();
  const [dismissed, setDismissed] = useState(false);
  if (!recoveryNotice || dismissed) return null;
  return (
    <div className="banner info recovery-banner" role="status">
      {msgRecoveryUsed(recoveryNotice.remaining)}
      <button type="button" className="link" onClick={() => setDismissed(true)}>
        Dismiss
      </button>
    </div>
  );
}
