# OneGuyOS Connect phone companion

## Product boundary

OG Connect keeps its existing KDE Connect-backed phone features working. The Android companion is an additive path for capabilities such as the new live screen stream; it does not replace the KDE Connect feature set. Do not remove or regress KDE Connect features as part of the companion work.

At startup, OG Connect should identify how each phone is connected: OG Connect companion, KDE Connect, or both. Show the available features for that phone and the connection source, and hide or disable unavailable actions with a plain explanation. When both paths refer to one handset, present one phone with the combined capabilities rather than duplicate cards. Pairing the two identities requires a user confirmation until a reliable match exists.

## End-user setup

1. On launch, the desktop checks for KDE Connect devices and OneGuyOS Connect companion advertisements, then shows the connection source and supported features per phone.
2. If the Android companion is not paired, offer one **Connect phone** action and a QR code / short install link. The user confirms the matching device once; the apps save paired keys and stable IDs.
3. The Android app explains and opens Android's own screen-share consent. For taps/swipes, it guides the user to enable the companion's Accessibility service. Notification mirroring and other restricted capabilities request their own Android permissions only when enabled.
4. The desktop shows a single phone card and capability status. It never exposes ADB, developer options, serial numbers, shell commands, or manual IP entry.

## Identity and transport

- Identity is a random app-install ID plus a paired public key, never a USB serial, network address, or KDE Connect ID.
- Discovery: Android NSD/mDNS on the same LAN; Wi-Fi Direct peer discovery when there is no shared LAN; Bluetooth Low Energy for nearby discovery and wake/control hints.
- Transport manager: prefer USB accessory/data transport when attached, then Wi-Fi LAN or Wi-Fi Direct. Bluetooth carries discovery, state, and small control messages; use it for a low-rate still preview only if tests show acceptable performance. Do not make it the normal screen-video path.
- Samsung DeX is a user-reported working screen-off projection path. Test it as an optional Samsung-specific output/transport on the S25 FE; it must not keep the phone panel awake. DeX cannot be the shared implementation for Motorola or other Android vendors.
- The active screen window belongs to the stable phone ID. A transport loss starts another available transport without replacing the phone object or closing the window. If no route is available, keep the paired card/window and show reconnecting.
- Never keep the phone display awake for mirroring. Preserve the connection and desktop window while the phone sleeps; show a clear screen-off/locked state and resume only after the user wakes and unlocks the phone. Do not trade battery life or burn-in risk for a continuously live preview.
- Each transport is authenticated with the paired key. Screen frames and control messages use a versioned, length-framed protocol; capability negotiation gates optional actions.

## Android capabilities

- Screen: MediaProjection in a foreground service, with clear explanation and Android's required user consent. A restarted capture session may require consent again.
- Interactive input: optional AccessibilityService, enabled by the user in Android Settings. Provide explicit status and an off switch.
- Notifications: optional NotificationListenerService consent.
- File sharing: Android Storage Access Framework; do not request broad storage access by default.
- Clipboard and background operation follow current Android restrictions and must report unsupported states instead of pretending success.

## Build sequence

1. Create the Android app and desktop protocol with stable identity, secure pairing, NSD discovery, capability/status messages, and reconnecting state.
2. Implement Wi-Fi screen streaming and desktop rendering, then touch input behind the optional Accessibility service.
3. Add USB transport and verify USB-to-Wi-Fi handoff without changing the desktop window.
4. Add Bluetooth discovery/control fallback and evaluate whether low-rate still frames are useful.
5. Preserve KDE Connect-backed actions. Add companion capabilities alongside them and merge the feature list per phone. Consider replacing an individual KDE Connect action only after the companion has parity and the owner reviews the change.

## Current constraints

The existing `backend/service.py` uses KDE Connect D-Bus for the original phone features. The Android companion prototype can advertise identity and stream an owner-approved screen over same-subnet Wi-Fi; OG Connect now discovers, pairs, and renders that stream in its existing phone panel. Pairing still uses a six-digit code over unencrypted TCP, discovery requires the companion screen-sharing service to be active, and this stream is view-only. The pre-launch connection-source/capability screen, secure persistent pairing, interactive Android control, USB/Bluetooth handoff, and iPhone companion are not implemented. Android build tools and source are now present; see `BUILD-STATUS.md` for live test evidence.
