# Attribution and third-party components

Telemost VPN additions: copyright 2026 bibibobo479, MIT.

The original source is [kulikov0/whitelist-bypass](https://github.com/kulikov0/whitelist-bypass),
commit `a26b5555728e5b3e990b8204f7663cc742c9de3b`, version 0.5.1, MIT.
The original copyright notice is preserved in `licenses/LICENSE.whitelist-bypass`.
The original Android and Electron interfaces and Go relay are adapted here.

The checked-in `android-app/app/libs/mobile.aar` is the **unmodified upstream
artifact** from that commit (SHA-256
`500a05c7fb187f13c5bdca280f257d7524e72fa9b10a316a3131672b6829d384`).
Its Go binding source is in `relay/androidbind`; it provides the Android TUN bridge.
The separate `librelay.so` executables are rebuilt from this repository and include
our KCP payload fix. See BUILDING.md to rebuild the AAR with the Android NDK.

Windows packages include the unmodified official Wintun 0.14.1 AMD64 DLL.
It is used only through its public API. Its prebuilt-binary license is in
`licenses/LICENSE.wintun` and is included in Windows resources; it is not MIT.

Linux packages include tun2socks 2.7.0, MIT (`licenses/LICENSE.tun2socks`).
The Go dependencies are pinned by `go.mod` and `go.sum`; they retain their licenses.
The Windows runtime is Electron (MIT), with Chromium and third-party notices included
by Electron in `LICENSE` and `LICENSES.chromium.html` in the packaged application.
Java/Kotlin dependencies, AndroidX, Material and ZXing retain their own licenses;
see `android-app/gradle/libs.versions.toml` and the Android build dependencies.

MIT licensing of our source does not relicense third-party code or binaries.
