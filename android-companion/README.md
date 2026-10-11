# OneGuyOS Connect Android companion (prototype)

This is the first Android companion slice for testing a user-approved local screen preview. It is separate from the existing KDE Connect/ADB path in the desktop app.

## Current prototype behavior

- Shows the Android manufacturer and model.
- Announces a stable random phone ID and model over local Wi-Fi while the companion app is open; the desktop dashboard can discover it without KDE Connect.
- Starts screen capture only after the user taps **Share phone screen** and accepts Android's screen-capture prompt.
- Runs capture in Android's required foreground service, advertises a local TCP screen service with Android NSD plus IPv4 UDP broadcast fallback for Wi-Fi hotspots that filter mDNS, and asks the desktop viewer to enter a six-digit code shown in the app.
- Streams JPEG frames over the same local network to `desktop/companion_preview.py`.
- Stops capture when Android stops the MediaProjection session, including when the phone locks. The phone is never kept awake.

This is a development prototype, not a release-ready pairing channel. The one-time code is not a substitute for encrypted transport; add authenticated TLS and persistent paired keys before regular use. Wi-Fi discovery is implemented; Bluetooth discovery, interactive touch, automatic screen pairing inside OG Connect, DeX integration, reconnect/handoff across USB/Wi-Fi, and capability-based notifications/files are not implemented yet. Discovery currently requires the companion app to be open on each phone.

## Build

The Gradle project uses Android Gradle Plugin 8.10.1, Kotlin 2.1.20, JDK 17+, and Android SDK 35. Set `JAVA_HOME` and `ANDROID_HOME`, then run:

```sh
./gradlew assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/app-debug.apk`.

## Desktop preview test

The viewer discovers the app over mDNS or same-subnet IPv4 broadcast, then asks for the six-digit code displayed on the phone. Broadcast discovery does not cross routers or subnets. It needs GTK 4/PyGObject and the Python dependency in `desktop/requirements.txt`.

From the `desktop` folder, install the small discovery dependency with `uv pip install --target .vendor -r requirements.txt`, then run `python3 companion_preview.py`.
