import { AuthDialog } from "./AuthDialog";
import { EnrollScreen } from "./EnrollScreen";

interface Props {
  /** Closes the dialog. Called on Cancel and after the recovery codes are acknowledged. */
  onClose(): void;
  /** Re-read the account status (the user is now enrolled). */
  onEnrolled(): void;
}

/** Voluntary enrollment from the account menu: `EnrollScreen` in password mode inside a dialog. */
export function EnableTotpDialog({ onClose, onEnrolled }: Props) {
  return (
    <AuthDialog title="Enable two-factor authentication">
      <EnrollScreen
        mode="voluntary"
        onLeave={onClose}
        onDone={() => {
          onEnrolled();
          onClose();
        }}
      />
    </AuthDialog>
  );
}
