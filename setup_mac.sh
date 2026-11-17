#!/bin/bash
# One-time setup on macOS: builds the open-source Mini VCI driver (rmvci)
# and puts librmvci_j2534.dylib next to live.py.
# Run from inside the ls430-live folder:   bash setup_mac.sh
set -e
cd "$(dirname "$0")"
RMVCI_COMMIT=618491b4ecfe9caed91987e9457c2e6138b8ba38   # version tested with this project

# 1. Apple's command-line developer tools (compiler, git)
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Apple command-line tools. A window will pop up: click Install,"
  echo "wait for it to finish, then run this script again."
  xcode-select --install
  exit 0
fi

# 2. Rust (the language rmvci is written in)
if ! command -v cargo >/dev/null 2>&1; then
  [ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "Installing Rust..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

# 3. Get and build rmvci
if [ ! -d rmvci ]; then
  git clone https://github.com/FletcherD/rmvci rmvci
fi
git -C rmvci fetch -q origin
git -C rmvci checkout -q "$RMVCI_COMMIT"
(cd rmvci && cargo build -p rmvci-j2534 --release)
cp rmvci/target/release/librmvci_j2534.dylib .

echo
echo "Built librmvci_j2534.dylib"
ls /dev/cu.usbserial-* 2>/dev/null && echo "Mini VCI detected." \
  || echo "Plug in the Mini VCI, then check: ls /dev/cu.usbserial-*"
echo "Next: python3 live.py --transport mvci --seconds 60"
