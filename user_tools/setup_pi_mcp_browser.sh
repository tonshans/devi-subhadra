#!/usr/bin/env bash
# ==============================================================================
# Setup & Optimasi Playwright MCP Headless untuk Raspberry Pi 4 (RAM 4GB)
# Fitur: ZRAM Swap, GPU Mem Min, Limit Maksimal 4 Tab, Auto-Close Idle Tab (3m)
# ==============================================================================

set -e # Hentikan skrip otomatis jika terjadi error pada salah satu langkah

CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo "~$CURRENT_USER")
GUARD_DIR="$HOME_DIR/mcp-browser-guard"

echo "=================================================================="
echo "Memulai Setup Playwright MCP + Browser Guard di Raspberry Pi 4"
echo "User aktif: $CURRENT_USER | Direktori: $GUARD_DIR"
echo "=================================================================="

# ------------------------------------------------------------------------------
# STEP 1: Validasi Arsitektur OS (Wajib 64-bit / aarch64)
# ------------------------------------------------------------------------------
echo "[STEP 1/7] Memeriksa arsitektur sistem operasi..."
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
  echo "ERROR: Arsitektur terdeteksi adalah '$ARCH'. Skrip ini membutuhkan OS 64-bit (aarch64)."
  exit 1
fi
echo "OK: Arsitektur 64-bit ($ARCH) terverifikasi."

# ------------------------------------------------------------------------------
# STEP 2: Update Paket & Aktifkan ZRAM (Kompresi RAM agar SD Card awet)
# ------------------------------------------------------------------------------
echo "[STEP 2/7] Memperbarui sistem dan mengonfigurasi ZRAM Swap (50% RAM)..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y zram-tools curl

# Mengatur ZRAM menggunakan algoritma zstd dan mengalokasikan 50% kapasitas RAM (~2GB)
echo -e "ALGO=zstd\nPERCENT=50" | sudo tee /etc/default/zramswap > /dev/null
sudo systemctl restart zramswap
echo "OK: ZRAM aktif."

# ------------------------------------------------------------------------------
# STEP 3: Kurangi Alokasi Memori GPU (Karena Mode Headless Tanpa Monitor)
# ------------------------------------------------------------------------------
echo "[STEP 3/7] Mengurangi alokasi VRAM GPU menjadi 16MB..."
BOOT_CONFIG=""
if [ -f "/boot/firmware/config.txt" ]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [ -f "/boot/config.txt" ]; then
  BOOT_CONFIG="/boot/config.txt"
fi

if [ -n "$BOOT_CONFIG" ]; then
  # Hapus baris gpu_mem lama jika ada, lalu tambahkan gpu_mem=16
  sudo sed -i '/^gpu_mem=/d' "$BOOT_CONFIG"
  echo "gpu_mem=16" | sudo tee -a "$BOOT_CONFIG" > /dev/null
  echo "OK: gpu_mem=16 ditambahkan ke $BOOT_CONFIG (efektif setelah reboot)."
else
  echo "PERINGATAN: File config.txt tidak ditemukan, melewati pengaturan gpu_mem."
fi

# ------------------------------------------------------------------------------
# STEP 4: Instal Node.js 22 LTS, Chromium Bawaan ARM64, dan Playwright MCP
# ------------------------------------------------------------------------------
echo "[STEP 4/7] Menginstal Node.js 22 LTS, Chromium, dan @playwright/mcp..."
if ! command -v node &> /dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt 18 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt install -y nodejs
fi

sudo apt install -y chromium
sudo npm install -g @playwright/mcp@latest
echo "OK: Node $(node -v), Chromium, dan playwright-mcp terpasang."

# ------------------------------------------------------------------------------
# STEP 5: Membuat Skrip Pengawas Browser (Limit 4 Tab & Auto-Close Idle 3 Menit)
# ------------------------------------------------------------------------------
echo "[STEP 5/7] Membuat skrip Browser Guard di $GUARD_DIR/guard.js..."
mkdir -p "$GUARD_DIR"

cat << 'EOF' > "$GUARD_DIR/guard.js"
const { spawn } = require('child_process');
const http = require('http');

const MAX_TABS = 4;                     // Maksimal tab terbuka bersamaan
const IDLE_TIMEOUT_MS = 3 * 60 * 1000;  // Tutup tab jika tidak dipakai > 3 menit
const CDP_PORT = 9222;                  // Port lokal Chrome DevTools Protocol

// Argumen Chromium untuk meminimalkan penggunaan RAM dan CPU di Raspberry Pi
const chromeArgs = [
  '--headless=new',
  `--remote-debugging-port=${CDP_PORT}`,
  '--remote-debugging-address=127.0.0.1',
  '--no-sandbox',
  '--disable-dev-shm-usage',
  '--disable-gpu',
  '--disable-extensions',
  '--disable-background-networking',
  '--disable-sync',
  '--metrics-recording-only',
  '--mute-audio',
  '--window-size=1024,768',
  '--user-data-dir=/tmp/chromium-mcp-profile'
];

const browser = spawn('/usr/bin/chromium', chromeArgs, { stdio: 'ignore' });
browser.on('exit', (code) => process.exit(code || 0));

// Menyimpan catatan waktu terakhir setiap tab diakses
const tabLastSeen = new Map();

function cdpRequest(path) {
  return new Promise((resolve, reject) => {
    http.get({ hostname: '127.0.0.1', port: CDP_PORT, path }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch { resolve(data); }
      });
    }).on('error', reject);
  });
}

// Loop pengecekan setiap 5 detik
setInterval(async () => {
  try {
    const targets = await cdpRequest('/json/list');
    if (!Array.isArray(targets)) return;

    const pages = targets.filter(t => t.type === 'page');
    const now = Date.now();
    const activeIds = new Set(pages.map(p => p.id));

    // Hapus catatan ID tab yang sudah ditutup manual oleh agent
    for (const id of tabLastSeen.keys()) {
      if (!activeIds.has(id)) tabLastSeen.delete(id);
    }

    // Perbarui waktu akses (tab indeks ke-0 di /json/list adalah tab aktif saat ini)
    pages.forEach((page, index) => {
      if (!tabLastSeen.has(page.id) || index === 0) {
        tabLastSeen.set(page.id, { time: now, url: page.url });
      }
    });

    // ATURAN 1: Auto-close tab idle melebihi IDLE_TIMEOUT_MS (sisakan minimal 1 tab)
    for (const page of pages) {
      if (pages.length <= 1) break;
      const info = tabLastSeen.get(page.id);
      if (info && (now - info.time > IDLE_TIMEOUT_MS) && page.url !== 'about:blank') {
        await cdpRequest(`/json/close/${page.id}`);
        tabLastSeen.delete(page.id);
      }
    }

    // ATURAN 2: Enforce batas maksimal MAX_TABS (tutup tab paling lama dibuka/diakses)
    const updatedTargets = await cdpRequest('/json/list');
    const updatedPages = Array.isArray(updatedTargets)
      ? updatedTargets.filter(t => t.type === 'page')
      : [];

    if (updatedPages.length > MAX_TABS) {
      const sortedByAge = updatedPages.sort((a, b) => {
        const timeA = tabLastSeen.get(a.id)?.time || 0;
        const timeB = tabLastSeen.get(b.id)?.time || 0;
        return timeA - timeB;
      });

      const excessCount = updatedPages.length - MAX_TABS;
      for (let i = 0; i < excessCount; i++) {
        await cdpRequest(`/json/close/${sortedByAge[i].id}`);
        tabLastSeen.delete(sortedByAge[i].id);
      }
    }
  } catch {
    // Abaikan error sementara saat Chromium baru saja mulai booting
  }
}, 5000);
EOF

node -c "$GUARD_DIR/guard.js"
echo "OK: Skrip guard.js berhasil dibuat dan lolos cek sintaks."

# ------------------------------------------------------------------------------
# STEP 6: Daftarkan Service Systemd dengan Hard-Limit RAM 1.8 GB
# ------------------------------------------------------------------------------
echo "[STEP 6/7] Membuat dan mengaktifkan systemd service (mcp-browser-guard)..."
sudo tee /etc/systemd/system/mcp-browser-guard.service > /dev/null << EOF
[Unit]
Description=Headless Chromium Guard (Max 4 Tabs & Auto-Close) for MCP
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
ExecStart=/usr/bin/node $GUARD_DIR/guard.js
Restart=always
RestartSec=3
MemoryMax=1800M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now mcp-browser-guard
sleep 3 # Beri waktu 3 detik agar Chromium selesai inisialisasi

# ------------------------------------------------------------------------------
# STEP 7: Verifikasi Endpoint CDP & Tampilkan Konfigurasi JSON untuk Agent
# ------------------------------------------------------------------------------
echo "[STEP 7/7] Memverifikasi koneksi Chromium CDP di port 9222..."
if curl -s http://127.0.0.1:9222/json/version | grep -q "Browser"; then
  echo "SUKSES: Chromium Headless berjalan aktif di http://127.0.0.1:9222"
else
  echo "PERINGATAN: Chromium belum merespons di port 9222. Cek log dengan: sudo journalctl -u mcp-browser-guard -n 20"
fi

echo ""
echo "=================================================================="
echo "SETUP SELESAI! Tambahkan konfigurasi berikut ke MCP Client Agent:"
echo "=================================================================="
cat << 'EOF'
{
  "mcpServers": {
    "playwright": {
      "command": "playwright-mcp",
      "args": [
        "--cdp-endpoint",
        "http://127.0.0.1:9222"
      ]
    }
  }
}
EOF
echo "=================================================================="
echo "CATATAN: Jalankan 'sudo reboot' sekali agar alokasi gpu_mem=16 aktif."
