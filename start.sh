#!/bin/bash
# Run from any directory without activating Conda in the caller's shell.
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

CONDA_BIN="${CONDA_EXE:-}"
if [[ ! -x "$CONDA_BIN" ]]; then
    CONDA_BIN="$(command -v conda || true)"
fi
if [[ ! -x "$CONDA_BIN" ]]; then
    CONDA_BIN="/opt/homebrew/bin/conda"
fi
if [[ ! -x "$CONDA_BIN" ]]; then
    printf '错误：未找到 Conda。请将 conda 加入 PATH，或设置 CONDA_EXE。\n' >&2
    exit 1
fi

CONDA_BASE="$("$CONDA_BIN" info --base)"
source "$CONDA_BASE/etc/profile.d/conda.sh"
if ! conda activate cryptolab; then
    printf '错误：无法激活 cryptolab 环境，请检查 conda env list。脚本不会创建或安装环境。\n' >&2
    exit 1
fi

python - <<'PY'
import errno
import socket
import sys

try:
    import streamlit
    from cryptolab.database import data_home
except ImportError as error:
    sys.exit(f"错误：cryptolab 环境缺少运行依赖：{error}。请检查项目安装。")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", 8501))
        listener.listen(1)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            sys.exit("错误：127.0.0.1:8501 端口已被占用。请关闭占用端口的服务后重试；未终止任何进程。")
        sys.exit(f"错误：无法绑定 127.0.0.1:8501：{error}")

print(f"Python：{sys.executable}")
print(f"数据目录：{data_home()}")
print("CryptoLab：http://127.0.0.1:8501（按 Ctrl+C 停止）")
PY

# exec delivers terminal signals directly to Streamlit. An explicit port also
# makes Streamlit fail if another process takes the port after our check.
exec python -u -m streamlit run "$PROJECT_DIR/web/app.py" \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true
