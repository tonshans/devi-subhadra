## jalankan dengan user yang menjalakan agent dan ada akses sudo

mkdir -p ~/mcp-browser-guard && cd ~/mcp-browser-guard
npm init -y
npm install ws

cat << 'EOF' > ~/mcp-browser-guard/guard.js
const { spawn } = require('child_process');
const http = require('http');

const MAX_TABS = 4;
const IDLE_TIMEOUT_MS = 3 * 60 * 1000; // 3 menit
const CDP_PORT = 9222;

// Jalankan Chromium dengan flag hemat memori untuk Raspberry Pi
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

// Loop pengawas setiap 5 detik
setInterval(async () => {
  try {
    const targets = await cdpRequest('/json/list');
    if (!Array.isArray(targets)) return;

    const pages = targets.filter(t => t.type === 'page');
    const now = Date.now();
    const activeIds = new Set(pages.map(p => p.id));

    // Bersihkan catatan tab yang sudah tertutup
    for (const id of tabLastSeen.keys()) {
      if (!activeIds.has(id)) tabLastSeen.delete(id);
    }

    // Catat atau perbarui waktu akses tab (tab paling atas di /json/list adalah yang aktif)
    pages.forEach((page, index) => {
      if (!tabLastSeen.has(page.id) || index === 0) {
        tabLastSeen.set(page.id, { time: now, url: page.url });
      }
    });

    // 1. Auto-close tab yang idle > IDLE_TIMEOUT_MS (sisakan minimal 1 tab blank agar browser tidak exit)
    for (const page of pages) {
      if (pages.length <= 1) break;
      const info = tabLastSeen.get(page.id);
      if (info && (now - info.time > IDLE_TIMEOUT_MS) && page.url !== 'about:blank') {
        await cdpRequest(`/json/close/${page.id}`);
        tabLastSeen.delete(page.id);
      }
    }

    // 2. Enforce batas maksimal MAX_TABS (tutup tab paling lama)
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
    // Abaikan jika browser masih dalam proses booting
  }
}, 5000);
EOF


sudo tee /etc/systemd/system/mcp-browser-guard.service << EOF
[Unit]
Description=Headless Chromium Guard (Max 4 Tabs & Auto-Close) for MCP
After=network.target

[Service]
Type=simple
User=$USER
ExecStart=/usr/bin/node /home/$USER/mcp-browser-guard/guard.js
Restart=always
RestartSec=3
MemoryMax=1800M

[Install]
WantedBy=multi-user.target
EOF


sudo systemctl daemon-reload
sudo systemctl enable --now mcp-browser-guard

echo ""
echo "arahkan konfigurasi MCP pada client agent kamu agar menyambung ke endpoint CDP [http://127.0.0.1:9222](http://127.0.0.1:9222) yang sudah dijaga oleh guard.js:"
echo ""
echo "{"
echo "  "mcpServers": {"
echo "    "playwright": {"
echo "      "command": 'playwright-mcp',"
echo "      "args": ["
echo "        '--cdp-endpoint',"
echo "        'http://127.0.0.1:9222'"
echo "      ]"
echo "    }"
echo "  }"
echo "}"
echo ""
