#!/usr/bin/env bash
#
# setup_vm.sh — Provision a fresh Debian/Ubuntu VM for the daily transport job.
#
# Usage:
#   chmod +x scripts/setup_vm.sh
#   sudo ./scripts/setup_vm.sh
#
# What it does:
#   1. Installs system packages (python3, ffmpeg, build-essential, etc.)
#   2. Installs yt-dlp & biliup via pip
#   3. Installs Ollama and pulls qwen2.5:7b
#   4. Builds the Go binary (yt-transfer)
#   5. Creates Python venv and installs requirements
#   6. Sets up systemd service for Ollama
#   7. Creates the daily cron job
#   8. Sets up log directory and rotation
#
set -euo pipefail

INSTALL_DIR="/opt/transport"
REPO_URL="${REPO_URL:-}"          # set if you want auto-clone
GO_VERSION="1.22.5"
PYTHON_MIN="3.11"
CRON_HOUR="${CRON_HOUR:-3}"       # UTC hour for daily job
CRON_MINUTE="${CRON_MINUTE:-0}"
UPLOAD_COUNT="${UPLOAD_COUNT:-2}"

# ---------- helpers ----------

info()  { echo -e "\033[1;32m[INFO]\033[0m  $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[1;31m[ERROR]\033[0m $*"; exit 1; }

need_root() {
    [[ $EUID -eq 0 ]] || error "Run this script as root (sudo)"
}

# ---------- Step 1: System packages ----------

install_system_deps() {
    info "Installing system packages..."
    apt-get update -qq
    apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        ffmpeg \
        build-essential \
        git curl wget \
        logrotate \
        sqlite3
}

# ---------- Step 2: Go ----------

install_go() {
    if command -v go &>/dev/null; then
        info "Go already installed: $(go version)"
        return
    fi
    info "Installing Go ${GO_VERSION}..."
    local arch
    arch=$(dpkg --print-architecture)  # amd64 or arm64
    wget -q "https://go.dev/dl/go${GO_VERSION}.linux-${arch}.tar.gz" -O /tmp/go.tar.gz
    rm -rf /usr/local/go
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
    # Add to path for this script
    export PATH="/usr/local/go/bin:$PATH"
    # Persist for all users
    cat > /etc/profile.d/go.sh <<'GOEOF'
export PATH="/usr/local/go/bin:$PATH"
GOEOF
    info "Go installed: $(go version)"
}

# ---------- Step 3: yt-dlp & biliup ----------

install_pip_tools() {
    info "Installing yt-dlp and biliup..."
    pip3 install --break-system-packages -q yt-dlp biliup 2>/dev/null \
        || pip3 install -q yt-dlp biliup
}

# ---------- Step 4: Ollama ----------

install_ollama() {
    if command -v ollama &>/dev/null; then
        info "Ollama already installed"
    else
        info "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi

    # Ensure systemd service exists and is enabled
    if systemctl list-unit-files | grep -q ollama.service; then
        info "Ollama systemd service already exists"
    else
        info "Creating Ollama systemd service..."
        cat > /etc/systemd/system/ollama.service <<'EOF'
[Unit]
Description=Ollama LLM Server
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=5
Environment="HOME=/root"

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
    fi

    systemctl enable --now ollama
    sleep 3  # give it time to start

    info "Pulling qwen2.5:7b model (this may take a while)..."
    ollama pull qwen2.5:7b
}

# ---------- Step 5: Project setup ----------

setup_project() {
    info "Setting up project directory at ${INSTALL_DIR}..."
    mkdir -p "${INSTALL_DIR}"

    # If REPO_URL is set and dir is empty, clone
    if [[ -n "$REPO_URL" && ! -d "${INSTALL_DIR}/.git" ]]; then
        info "Cloning repository..."
        git clone "$REPO_URL" "${INSTALL_DIR}"
    fi

    if [[ ! -d "${INSTALL_DIR}/ml-service" ]]; then
        error "No ml-service directory found at ${INSTALL_DIR}. Copy or clone the repo first."
    fi

    # Build Go binary
    info "Building yt-transfer Go binary..."
    cd "${INSTALL_DIR}"
    if [[ -f go.mod ]]; then
        go build -o yt-transfer ./cmd/... 2>/dev/null \
            || go build -o yt-transfer . 2>/dev/null \
            || warn "Go build failed — place a pre-built yt-transfer binary in ${INSTALL_DIR}"
    fi

    # Python venv
    info "Creating Python venv..."
    cd "${INSTALL_DIR}/ml-service"
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt -q

    # Directories
    mkdir -p "${INSTALL_DIR}/logs"
    mkdir -p "${INSTALL_DIR}/ml-service/models"
}

# ---------- Step 6: Cron ----------

setup_cron() {
    info "Setting up daily cron job at ${CRON_HOUR}:${CRON_MINUTE} UTC..."

    local cron_line="${CRON_MINUTE} ${CRON_HOUR} * * * cd ${INSTALL_DIR}/ml-service && ${INSTALL_DIR}/ml-service/.venv/bin/python real_run.py --upload >> ${INSTALL_DIR}/logs/cron.log 2>&1"

    # Remove old entries, add new one
    ( crontab -l 2>/dev/null | grep -v "daily_job\|real_run" ; echo "$cron_line" ) | crontab -

    info "Cron installed:"
    crontab -l | grep real_run
}

# ---------- Step 7: Log rotation ----------

setup_logrotate() {
    info "Setting up log rotation..."
    cat > /etc/logrotate.d/transport <<EOF
${INSTALL_DIR}/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
}
EOF
}

# ---------- Step 8: Environment file ----------

setup_env() {
    info "Creating environment file..."
    local env_file="${INSTALL_DIR}/ml-service/.env"
    if [[ ! -f "$env_file" ]]; then
        cat > "$env_file" <<EOF
# Transport daily job configuration
# Uncomment and set your YouTube API key:
# YOUTUBE_API_KEY=your-key-here
DB_PATH=${INSTALL_DIR}/ml-service/data.db
TRANSPORT_BINARY=${INSTALL_DIR}/yt-transfer
BILIUP_COOKIE=${INSTALL_DIR}/cookies.json
UPLOAD_COUNT=${UPLOAD_COUNT}
MODEL_DIR=${INSTALL_DIR}/ml-service/models
LOG_DIR=${INSTALL_DIR}/logs
LLM_MODEL=qwen2.5:7b
EOF
        info "Edit ${env_file} to set your YOUTUBE_API_KEY"
    else
        info "Environment file already exists, skipping"
    fi
}

# ---------- Main ----------

main() {
    need_root

    info "=============================="
    info "  Transport VM Setup"
    info "=============================="

    install_system_deps
    install_go
    install_pip_tools
    install_ollama
    setup_project
    setup_cron
    setup_logrotate
    setup_env

    echo
    info "=============================="
    info "  Setup complete!"
    info "=============================="
    echo
    info "Next steps:"
    info "  1. Copy cookies.json (biliup auth) to ${INSTALL_DIR}/cookies.json"
    info "  2. Copy trained model to ${INSTALL_DIR}/ml-service/models/"
    info "  3. Edit ${INSTALL_DIR}/ml-service/.env to set YOUTUBE_API_KEY"
    info "  4. Test with: cd ${INSTALL_DIR}/ml-service && .venv/bin/python real_run.py --dry-run"
    info "  5. Cron runs daily at ${CRON_HOUR}:${CRON_MINUTE} UTC"
}

main "$@"
