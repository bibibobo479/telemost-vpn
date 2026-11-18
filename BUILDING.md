# Build instructions

Use Go **1.26.1** (`GOTOOLCHAIN=go1.26.1`), Node.js 22/24, JDK 21 and the Android SDK.
Build artifacts go in `prebuilts/`; credentials and signing keys never belong in Git.
The Android Gradle wrapper downloads its pinned Gradle 8.13. SDK 36 and Build Tools
35 are required; Android Studio can install them. Read the source before building.

## Windows x64

```sh
export GOTOOLCHAIN=go1.26.1
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags='-s -w' \
  -o joiner-desktop-app/desktop-joiner/desktop-joiner-bundle.exe ./joiner-desktop-app/desktop-joiner
curl -fL https://www.wintun.net/builds/wintun-0.14.1.zip -o /tmp/wintun.zip
unzip -p /tmp/wintun.zip wintun/bin/amd64/wintun.dll > joiner-desktop-app/desktop-joiner/wintun-bundle.dll
cd joiner-desktop-app
npm ci
node node_modules/electron/install.js
npm run build
node --test test-sharing.cjs
npx electron-builder --win portable --x64 --publish never
```

Electron-builder may download packaging tools. Windows binaries are unsigned by
default. For a trusted signed release use your own code-signing certificate.
The Go process restores its routes when the GUI sends `QUIT`; killing processes
externally is not equivalent to pressing Stop.

## Android

Rebuild the separate relay executable for both supported phone architectures:

```sh
export GOTOOLCHAIN=go1.26.1
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags='-s -w' \
  -o android-app/app/src/main/jniLibs/arm64-v8a/librelay.so ./relay
CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build -trimpath -ldflags='-s -w' \
  -o android-app/app/src/main/jniLibs/armeabi-v7a/librelay.so ./relay
cd android-app
export ANDROID_HOME=/path/to/Android/Sdk
export JAVA_HOME=/path/to/jdk-21
./gradlew assembleDebug
```

For a release, create a **private** keystore outside this repository, alias
`telemost-vpn`. Set `TELEMOST_SIGNING_STORE` to its absolute path and
`TELEMOST_SIGNING_PASSWORD_FILE` to a private file containing its password, then
run `./gradlew assembleRelease`. Without these variables release output is unsigned.
Keep the key to update installed APKs with the same signature. The author's release
key is intentionally not published.

The upstream `mobile.aar` TUN binding is included with a recorded checksum in
THIRD_PARTY.md. To rebuild it, install Android NDK, set `ANDROID_NDK_HOME`, install
`gomobile` and `gobind` at the exact `golang.org/x/mobile` version in `go.mod`, and run:

```sh
export CGO_LDFLAGS='-Wl,-z,max-page-size=16384'
gomobile bind -trimpath -ldflags='-s -w' -target=android -androidapi 23 \
  -o android-app/app/libs/mobile.aar ./relay/androidbind
```

## Linux bundle

```sh
python3 tools/build-linux.py
```

This builds the Go Creator/Joiner, downloads pinned tun2socks, and creates a portable
installer archive. To install the unpacked bundle, run `bash install.sh`.

## Tests

```sh
GOTOOLCHAIN=go1.26.1 go test ./relay/tunnel/rtc ./relay/tunnel
(cd linux && python3 -m unittest test_app.py test-discovery.py)
(cd joiner-desktop-app && npm run build && node --test test-sharing.cjs)
```

Android UI/emulator checks do not substitute for testing a real ARM phone and its
mobile network. Cross-compiling the Windows core does not verify Windows driver or
route behaviour. Release notes describe the checks actually performed.
