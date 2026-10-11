#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR=${1:-"$SOURCE_DIR/dist"}
VERSION=0.1.0~beta1
PACKAGE=oneguyos-connect
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT HUP INT TERM

ROOT="$STAGE/root"
APP="$ROOT/usr/lib/oneguyos-connect"
mkdir -p "$APP/backend" "$APP/ui" \
    "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
    "$ROOT/usr/share/doc/$PACKAGE" "$ROOT/DEBIAN"

cp "$SOURCE_DIR/oneguyos-connect.py" "$APP/"
cp "$SOURCE_DIR/backend/"*.py "$APP/backend/"
cp "$SOURCE_DIR/ui/"*.py "$APP/ui/"
cp "$SOURCE_DIR/ui/style.css" "$APP/ui/"
chmod 0644 "$APP"/*.py "$APP/backend/"*.py "$APP/ui/"*.py "$APP/ui/style.css"
cat > "$ROOT/usr/bin/oneguyos-connect" <<'LAUNCHER'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/oneguyos-connect/oneguyos-connect.py "$@"
LAUNCHER
chmod 0755 "$ROOT/usr/bin/oneguyos-connect"
install -m 0644 "$SCRIPT_DIR/org.oneguyos.connect.desktop" \
    "$ROOT/usr/share/applications/org.oneguyos.connect.desktop"

cat > "$ROOT/usr/share/doc/$PACKAGE/README.BETA" <<'BETA_README'
OneGuyOS Connect 0.1.0 beta

Install on Debian 13 or a compatible Debian/Ubuntu desktop:
  sudo apt install ./oneguyos-connect_0.1.0~beta1_all.deb

Launch from the Applications menu or run:
  oneguyos-connect

KDE Connect is required for paired-device controls and the phone file browser.
Install/open KDE Connect on the phone, pair it with the desktop, and enable its
file-system expose permission for phone file browsing. The companion app and
screen-sharing path are prototypes; use only on a trusted local network.

AI provider credentials and chat history are not included in this package.
The app reads/writes your per-user settings under ~/.config/oneguyos-connect.
When you configure a cloud AI provider, prompts and selected chat content are
sent to that provider. Local providers send requests to the configured local
endpoint. The package does not bundle chat transcripts, API keys, device IDs,
phone profiles, screenshots, or build records.

To remove the beta:
  sudo apt remove oneguyos-connect
BETA_README
chmod 0644 "$ROOT/usr/share/doc/$PACKAGE/README.BETA"

install -m 0644 "$SCRIPT_DIR/debian/control" "$ROOT/DEBIAN/control"
find "$ROOT" -type d -exec chmod 0755 {} +

mkdir -p "$OUTPUT_DIR"
dpkg-deb --build --root-owner-group "$ROOT" \
    "$OUTPUT_DIR/${PACKAGE}_${VERSION}_all.deb" >/dev/null
printf '%s\n' "$OUTPUT_DIR/${PACKAGE}_${VERSION}_all.deb"
