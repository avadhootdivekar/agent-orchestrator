import { authApi } from "../api";
import { ReauthDialog } from "./ReauthDialog";

interface Props {
  onClose(): void;
  /** Re-read the account status (2FA is now off). */
  onDisabled(): void;
}

/** E6: turn two-factor authentication off (password + a code). A 403 `totp_required` shows the server's reason. */
export function DisableTotpDialog({ onClose, onDisabled }: Props) {
  return (
    <ReauthDialog
      title="Disable two-factor authentication"
      lede="Enter your password and a current authentication (or recovery) code. Your other sessions will be signed out."
      submitLabel="Disable"
      action={authApi.disableTotp}
      onSuccess={() => {
        onDisabled();
        onClose();
      }}
      onCancel={onClose}
    />
  );
}
