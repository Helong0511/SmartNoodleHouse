#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v cargo >/dev/null || { echo "错误：未找到 cargo，请安装 Rust toolchain。"; exit 1; }
command -v rustc >/dev/null || { echo "错误：未找到 rustc，请安装 Rust toolchain。"; exit 1; }

echo "=== 广告追踪引擎 Linux 构建 ==="
echo "OS: $(uname -s) $(uname -m)"
echo "CPU: $(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"

cargo fmt -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all-targets
if [ -f Cargo.lock ]; then cargo build --release --locked; else cargo build --release; fi
mkdir -p lib
cp -f target/release/libad_tracking_engine.so lib/
sha256sum lib/libad_tracking_engine.so > lib/libad_tracking_engine.so.sha256

echo "构建完成：lib/libad_tracking_engine.so"
