#!/bin/bash
# Build "LS430 Live.app" on this Mac and install it into Applications.
# Usage (from the project folder):   bash packaging/build_mac.sh
set -e
cd "$(dirname "$0")/.."
RMVCI_COMMIT=618491b4ecfe9caed91987e9457c2e6138b8ba38

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Apple command-line tools. Click Install in the window, then run this again."
  xcode-select --install; exit 0
fi
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
if ! command -v cargo >/dev/null 2>&1; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

echo "== 1/3 Building the Mini VCI driver (rmvci)"
[ -d rmvci ] || git clone -q https://github.com/FletcherD/rmvci rmvci
git -C rmvci fetch -q origin && git -C rmvci checkout -q "$RMVCI_COMMIT"
(cd rmvci && cargo build -q -p rmvci-j2534 --release)
cp rmvci/target/release/librmvci_j2534.dylib .

echo "== 2/3 Packaging the app"
python3 -m venv .build-venv
.build-venv/bin/pip install -q --upgrade pip
.build-venv/bin/pip install -q -r packaging/requirements.txt
.build-venv/bin/pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/LS430Live.spec

echo "== 3/3 Installing"
DEST=/Applications
[ -w "$DEST" ] || DEST="$HOME/Applications"; mkdir -p "$DEST"
rm -rf "$DEST/LS430 Live.app"
ditto "dist/LS430 Live.app" "$DEST/LS430 Live.app"
echo "Installed: $DEST/LS430 Live.app  (recordings go to ~/Documents/LS430 Live/logs)"
open "$DEST/LS430 Live.app"
