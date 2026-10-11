# OneGuyOS Connect

OneGuyOS Connect brings phone controls, screen tools, and optional AI chat and recap features into one Linux desktop app. It works with the existing KDE Connect service and is being developed as a beta.

## Beta download

The current Debian 13 beta is available on the [GitHub Releases page](https://github.com/OneGuyOS/OneGuyOS-Connect/releases/tag/v0.1.0-beta1):

- [Download the Debian package](https://github.com/OneGuyOS/OneGuyOS-Connect/releases/download/v0.1.0-beta1/oneguyos-connect_0.1.0-beta1_all.deb)
- [Download its SHA-256 file](https://github.com/OneGuyOS/OneGuyOS-Connect/releases/download/v0.1.0-beta1/oneguyos-connect_0.1.0-beta1_all.deb.sha256)

Download and verify the package, then install it:

```sh
wget https://github.com/OneGuyOS/OneGuyOS-Connect/releases/download/v0.1.0-beta1/oneguyos-connect_0.1.0-beta1_all.deb
wget https://github.com/OneGuyOS/OneGuyOS-Connect/releases/download/v0.1.0-beta1/oneguyos-connect_0.1.0-beta1_all.deb.sha256
sha256sum -c oneguyos-connect_0.1.0-beta1_all.deb.sha256
sudo apt install ./oneguyos-connect_0.1.0-beta1_all.deb
```

Launch **OneGuyOS Connect** from your applications menu or run:

```sh
oneguyos-connect
```

The package is built for Debian 13 (Trixie). It installs KDE Connect and the desktop runtime dependencies through APT. Other Debian-based distributions may work but have not been verified.

## KDE Connect and the Android companion

The desktop app uses KDE Connect for its existing pairing, device controls, notifications, clipboard, file sending, and phone-storage browsing. Install KDE Connect on the Android phone and pair it with KDE Connect on the desktop. Phone storage browsing also requires filesystem access to be enabled in KDE Connect on the phone.

The optional OneGuyOS Android companion is a separate prototype for screen sharing. Its Android package ID is `org.oneguyos.connect.companion`, so it can be installed alongside KDE Connect. It is not a KDE Connect plugin and does not replace KDE Connect. The APK is not attached to the current Debian release; Android build instructions are in [`android-companion/README.md`](android-companion/README.md).

The companion screen stream currently uses cleartext transport on the local network with a short pairing code. Only test it on a trusted network, and stop sharing when finished.

## Current beta scope

The desktop app includes connected-device controls, notifications, file sending and browsing, phone-screen preview, and optional AI chat and daily recap integrations. Screen preview routes and capabilities depend on the phone and available desktop tools. Phone-storage browsing may fail when KDE Connect's SFTP service on the phone is unavailable or has not been granted access.

AI providers are configured by the user. If a cloud provider is selected, the prompts and chat or recap content submitted to it are sent to that provider. Local provider requests go to the configured local endpoint. The beta package does not contain API keys, saved chats, phone identifiers, screenshots, or machine-specific configuration.

This is an early beta. Some device-specific and companion features are still being tested; please report issues through [GitHub Issues](https://github.com/OneGuyOS/OneGuyOS-Connect/issues) with your Linux distribution and relevant phone model, and omit private chat contents, pairing codes, and API keys.

## Development

The desktop app uses Python, GTK 4, and Libadwaita. On Debian 13, install the same runtime dependencies listed in [`packaging/debian/control`](packaging/debian/control), then run:

```sh
python3 oneguyos-connect.py
```

For the Android companion's requirements and build steps, see [`android-companion/README.md`](android-companion/README.md).
