import { authApi } from "../api";
import { ReauthDialog } from "./ReauthDialog";
import { RecoveryCodes } from "./RecoveryCodes";

interface Props {
  onClose(): void;
  /** Re-read the account status (the remaining-codes count changed). */
  onRegenerated(): void;
}

/** E7: replace the recovery codes; the new ones appear behind the acknowledgement gate. */
export function RegenerateCodesDialog({ onClose, onRegenerated }: Props) {
  return (
    <ReauthDialog
      title="Regenerate recovery codes"
      lede="Enter your password and a current authentication code. The old recovery codes stop working."
      submitLabel="Regenerate"
      action={authApi.regenerateRecoveryCodes}
      onSuccess={(result) => {
        onRegenerated();
        return (
          <RecoveryCodes
            codes={result.recovery_codes}
            onContinue={onClose}
          />
        );
      }}
      onCancel={onClose}
    />
  );
}
