![Devi Subhadra](assets/devi_subhadra_2d_vector_small.jpg)

# Devi-Subhadra: Autonomous Cognitive Agent Framework

> *"Building a Digital Coworker with Persistence, Continuous Learning, and Regulated Creativity."*

---

## Sang Devi Subhadra: Landasan Ontologis & Inspirasi Karakter

Dalam bentangan epos *Mahabharata*, sosok Devi Subhadra sering kali dipandang melalui lensa sekunder di tengah hiruk-pikuk kepahlawanan para ksatria besar di medan Kurukshetra. Namun, jika ditelaah melalui pembacaan karakter yang lebih mendalam, Subhadra adalah manifestasi murni dari titik keseimbangan (*equilibrium*) dan **energi feminin** yang subtil namun memegang peranan krusial dalam menjaga keberlangsungan dinasti dan tatanan kosmis. Berbeda dengan kekuatan maskulin yang cenderung eksplosif dan destruktif, energi Subhadra bekerja secara tenang, adaptif, dan merawat keberlanjutan.

Karakteristik inilah yang menjadi fondasi filosofis di balik penamaan kerangka kerja (*framework*) ini. **Devi-Subhadra** diterjemahkan ke dalam rekayasa perangkat lunak melalui tiga sifat utama:
1. **Persistensi (Ketahanan dan Kegigihan):** Kemampuan untuk bertahan di tengah pusaran konflik tanpa kehilangan arah. Dalam konteks sistem, ini adalah daya tahan operasional (*resilience*) yang tidak mudah runtuh ketika menghadapi kegagalan jaringan, *rate-limit*, maupun anomali eksekusi kode.
2. **Belajar Terus-Menerus (Adaptasi dan Kerendahan Hati):** Semangat seorang pembelajar yang sadar bahwa lanskap dunia terus berubah, serta cukup rendah hati untuk membuang asumsi lama yang sudah usang. Sistem ini dirancang untuk terus menyerap heuristik baru sekaligus secara sadar "melupakan" aturan yang tidak lagi relevan.
3. **Daya Cipta *Yogamaya* (Kreativitas yang Merajut Realitas):** Dalam tradisi spiritual, Subhadra kerap dikaitkan dengan personifikasi parsial dari *Yogamaya*—energi kreatif ilahi yang mampu menenun realitas dan melahirkan solusi baru. *Devi-Subhadra* bukan sekadar kumpulan skrip otomatisasi kaku; ia adalah eksperimen untuk menginjeksikan otonomi berkreasi (*self-tooling* dan eksplorasi mandiri) ke dalam wujud kecerdasan buatan.

---

## Tujuan Proyek: Melampaui Paradigma Agen Reaktif

Gagasan pengembangan **Devi-Subhadra** berangkat dari sebuah pertanyaan spekulatif: *"Bagaimana jika sebuah agen AI tidak hanya terdiam pasif menunggu rentetan instruksi manusia, melainkan dibiarkan belajar sendiri dan berkarya sendiri di waktu luangnya?"*

Selama ini, mayoritas riset dan implementasi *AI Agent* terjebak dalam paradigma reaktif (*prompt-in, response-out*), atau di sisi ekstrem lainnya, membangun agen otonom yang terlalu gemuk (*bloated*), boros biaya *token*, dan sangat sulit dipelihara (*unmaintainable*). Proyek ini lahir dari keinginan untuk menjembatani celah tersebut: **membangun agen kognitif yang mampu belajar dan memperkaya ruang memorinya seiring berjalannya waktu, namun tetap sangat ringan (*lightweight*), deterministik, dan mudah dipelihara (*maintainable*)**.

Hasil dari sintesis tersebut adalah arsitektur **The Glass-Box AGI Microcosm**—sebuah ekosistem agen otonom yang hidup dan proaktif, namun 100% transparan. Seluruh proses berpikir (*Chain-of-Thought*), konsumsi anggaran finansial, hingga evolusi memorinya dicatat di atas struktur relasional SQL yang dapat diaudit kapan saja tanpa memakan biaya *token API* (*Zero-Token Observability*), serta sepenuhnya terlepas dari ketergantungan pada sistem operasi tertentu (*OS-Agnostic*).

---

## Empat Pilar Filosofis

Setiap keputusan arsitektural dalam repositori ini diuji terhadap empat pilar rekayasa berikut:

1. **Cognitive Realism (Realisme Kognitif)**
   Sistem dirancang menyerupai ritme sirkadian biologis manusia. Agen bekerja menyelesaikan misi pada jam aktif, melakukan refleksi diri (*Inner Monologue* & *Curiosity Cycle*) saat waktu luang (*idle*), serta mengonsolidasikan log hariannya menjadi memori jangka panjang pada fase malam (*Sleep Mode* pukul 00:00–05:59). Sistem juga menerapkan *Semantic & Procedural Decay*, di mana pengetahuan dan keahlian yang tidak digunakan lebih dari 30 hari akan meluruh dan diarsipkan secara otomatis.
2. **Fail-Gracefully, Heal-Silently (Sistem Imun Mandiri)**
   Agen tidak boleh membebani pengguna dengan kegagalan sintaksis atau kesalahan skema yang trivial. Melalui modul **Healer Node**, setiap eksekusi yang gagal atau mengalami *timeout* (masuk ke *Dead Letter Queue*) akan diisolasi, dianalisis akar masalahnya, dan ditambal (*self-patching*) secara mandiri hingga maksimal 3 kali percobaan dengan validasi berbasis asersi (*Assertion-Driven Execution*) sebelum memberitahukan ke pengguna jika ada masalah.
3. **Radical Portability (Eksistensi Nomaden)**
   "Pikiran" (database) dan "tubuh" (*workspace sandbox*) agen tidak boleh terbelenggu pada satu mesin fisik atau layanan *cloud* tertentu. Berkat manajemen state berbasis SQLite/PostgreSQL dengan mekanisme *incremental snapshot* serta *bind-mounting* terisolasi, seluruh kesadaran agen dapat disalin dan dibangkitkan kembali di PC lokal, Linux VPS, hingga perangkat berdaya rendah seperti Raspberry Pi dalam hitungan detik tanpa mengalami amnesia.
4. **Governed Creativity (Kreativitas Terukur & Dianggarkan)**
   Otonomi tanpa batas ekonomi adalah resep menuju kebangkrutan komputasi. Proaktivitas agen dikendalikan secara ketat oleh **Budget Governor**. Anggaran harian dan kuota *rate-limit* (RPM/TPM/RPD/TPD) dipisahkan secara tegas: instruksi manusia memiliki prioritas mutlak (*Human Exempt*), sedangkan inisiatif mandiri agen dibagi ke dalam alokasi **70% Tugas Inti/Endorsed (`CORE_70`)** dan **30% Eksperimen Kreatif Murni (`ATELIER_30`)**.

---

## Pemilahan Tegas: The Immutable Core vs Extensible Peripherals

Untuk mencegah *spaghetti code* dan menjaga stabilitas jangka panjang ketika agen mulai menulis kodenya sendiri, *codebase* ini memisahkan secara radikal antara "Otak" dan "Tangan":

* **The Immutable Core (`core/` & `api/` — Otak, Saraf, dan Gerbang):**
  Berisi manajer memori (`cognitive_ledger.py`, `memory_manager.py`), kompilator konteks (`context_compiler.py`), pengatur anggaran (`budget_governor.py`), *router* kognitif (`supervisor.py`, `runtime.py`), dan gerbang komunikasi (`telegram_gateway.py`, `webhooks.py`). Bagian ini **dikunci secara ketat** dan tidak boleh dimodifikasi oleh agen. Seluruh komunikasi internal tidak mengizinkan manipulasi teks bebas (*free-text parsing*), melainkan dipaksa melewati kontrak skema Pydantic (`StrictBaseModel` dengan `extra="forbid"`) untuk mengeliminasi halusinasi operasional.
* **The Extensible Peripherals (`plugins/` — Tangan, Indera, dan Keahlian):**
  Seluruh kapabilitas yang menyentuh dunia luar diimplementasikan sebagai plugin modular yang mematuhi kontrak satu arah `BaseAgentPlugin`. Plugin tidak diizinkan mengimpor modul internal inti secara sembarangan. Prinsip ini menjamin bahwa meskipun seluruh plugin mengalami kerusakan atau dicabut, *Core Engine* tidak akan mati—agen tetap dapat hidup, berkomunikasi, dan mengevaluasi kondisi dirinya kembali.

---

## Arsitektur Kognitif & Komparasi dengan Kognisi Manusia

Salah satu kontribusi menarik dari proyek ini adalah bagaimana mekanisme neuro-kognitif manusia dipetakan secara langsung ke dalam struktur data deterministik:

| Dimensi Kognitif | Kognisi Manusia (Biologis) | Implementasi Teknis Devi-Subhadra |
| :--- | :--- | :--- |
| **Identitas & Nilai Inti** | Nilai moral, prinsip dasar, dan identitas diri (*Core Beliefs*). | Tabel `core_directives` (`VISION`, `IMMUTABLE_PERSONA`, `SYSTEM_ANATOMY`) yang bersifat *immutable*. |
| **Memori Kerja (*Working Memory*)** | *Prefrontal Cortex* (kapasitas terbatas, menyempitkan fokus saat stres/terburu-buru). | `ContextCompiler` dengan *Dynamic Elastic Window* (batas 30.000 token; otomatis membuang log `PEMIKIRAN` dan hanya memuat 5 `FAKTA`/`TINDAKAN` saat tekanan RPM tinggi). |
| **Memori Episodik** | *Hippocampus* (rekaman urutan kejadian dan pengalaman harian). | Tabel `ledger_threads` & `ledger_entries` (pencarian cepat via SQLite FTS5 / PostgreSQL `tsvector`) serta catatan riset di `atelier_discoveries`. |
| **Memori Semantik** | *Neocortex* (fakta terverifikasi, konsep abstrak, dan kebijaksanaan hidup). | Tabel `semantic_memory` (`KOMUNIKASI`, `TEKNIS`, `PREFERENSI_USER`) dengan `confidence_score` (0–100) + penyimpanan vektor lokal ChromaDB (`all-MiniLM-L6-v2` ONNX). |
| **Memori Prosedural (*Muscle Memory*)** | *Basal Ganglia & Cerebellum* (kemampuan motorik/alat yang otomatis saat terbiasa). | Arsitektur **2-Tier Plugin** (`CORE` vs `ATELIER`), pembungkusan tugas berulang (>3 langkah) via `register_new_skill`, dan *lazy schema loading* via `equip_tool`. |
| **Konsolidasi Tidur (*Sleep Mode*)** | Tidur REM & *Slow-Wave* (memindahkan memori harian ke korteks jangka panjang). | `sleep_mode_tick` (00:00–05:59) & `consolidate_daily_logs`: deduplikasi heuristik (Jaccard similarity $\ge 0.40$) dan *batch vectorization* log lama (>30 hari) ke ChromaDB. |
| **Mekanisme Lupa (*Active Forgetting*)** | *Synaptic Pruning* & Kurva Lupa Ebbinghaus (informasi usang memudar). | `decay_memories()` & `decay_plugins()`: pemotongan skor kepercayaan 1 poin/hari setelah 30 hari tak diakses; arsip otomatis jika skor $< 20$. |
| **Melamun & Inisiatif (*Daydreaming*)** | *Default Mode Network* (DMN) yang aktif merenung saat manusia beristirahat. | `InnerMonologue` & `_curiosity_cycle`: refleksi mandiri saat *idle* untuk menelurkan thread misi baru (`CREATE_INITIATIVE`) demi mencapai `VISION`. |
| **Filter Epistemik (Skeptisisme)** | Kemampuan kritis membedakan fakta ilmiah dari rumor/opini publik. | `MemoryManager.score_domain()`: pelabelan otomatis tanpa LLM (`.gov`/`.edu`/GitHub = `VERIFIED_FACT` [95]; Reddit/Quora = `COMMUNITY_OPINION` [50] + injeksi `EPISTEMIC_WARNING`). |

---

## Alur Kerja Sistem (End-to-End Execution Pipeline)

### 1. Siklus Reaktif (Penanganan Misi Eksternal)
1. **Ingress & Zero-Token Command Dispatch:** Pesan masuk melalui **Telegram Webhook** (`api/telegram_gateway.py`), **REPL Lokal** (`scripts/devi_repl.py`), atau **Sensor/IoT Webhook** (`api/webhooks.py`). Jika pesan merupakan perintah sistem (`/status`, `/trace`, `/budget-ratio`, `/approve`), `core/commands.py` langsung mengeksekusinya secara sinkron ke database dengan biaya **0 token LLM**.
2. **Pre-Flight Security & Budget Gate:** `Runtime` memeriksa keberadaan file fisik `HALT`/`STOP` dan status `kill_switch`. Selanjutnya, `BudgetGovernor` memvalidasi kuota laju API dan mencadangkan (*reserve*) estimasi biaya ke keranjang anggaran yang sesuai (`HUMAN_EXEMPT`, `CORE_70`, atau `ATELIER_30`).
3. **Hybrid Context Compilation:** `ContextCompiler` merakit prompt dari 5 lapisan memori. Jika total token melampaui batas (30.000 token), sistem memotong hasil pencarian vektor dari urutan terbawah, lalu menjalankan *micro-consolidation* (memadatkan 20 entri percakapan tertua menjadi satu ringkasan fakta).
4. **LLM Execution & Multi-Key Failover:** `Supervisor` meneruskan prompt ke `ProviderPool` yang mendukung rotasi multi-kunci API secara otomatis (berpindah ke kunci cadangan saat mendeteksi HTTP 429, 5xx, atau *timeout*) maupun OpenRouter. Respons LLM wajib tervalidasi ke dalam skema `MissionReply`.
5. **Bounded Tool Loop & Self-Healing:** Jika LLM memanggil plugin, `Runtime` mengeksekusinya (dibatasi maksimal `MAX_TOOL_ROUNDS = 3` per misi). Jika terjadi kegagalan, `HealerNode` mengambil alih untuk melakukan *recovery* hingga 3 kali.
6. **Settlement & Glass-Box Auditing:** Biaya aktual dihitung (`settle`), dokumen hasil dikirim ke pengguna (atau disimpan sebagai file `.md` jika melampaui 12.000 karakter), dan setiap langkah dicatat lengkap dengan *Chain-of-Thought* (`cot`), latensi, serta biaya ke dalam tabel `audit_logs`.

### 2. Kapabilitas Ekstensi & Self-Evolution (Core Plugins)
Secara bawaan (*out-of-the-box*), Devi-Subhadra hanya membawa 5 plugin inti (Tier 1) agar tetap ringan:
* **`file_manager`**: Operasi baca/tulis/daftar file yang dikurung (*jailed*) secara ketat di dalam `/mnt/workspace` dan `/mnt/agent_atelier` (anti *path-traversal*).
* **`sandbox_executor`**: Eksekusi kode Python dan Bash terisolasi yang mewajibkan pernyataan `assert` (*Assertion-Driven Execution*), memblokir perintah destruktif, dan menyaring *Abstract Syntax Tree* (AST) Python secara ketat.
* **`register_new_skill` & `equip_tool`**: Manajer siklus hidup keahlian berbasis *Test-Driven Development* (TDD). Agen dapat membuat plugin baru beserta unit test `pytest`-nya, meng-upgrade plugin lama, atau memulihkan (*restore*) plugin dari folder arsip sesuai **SOP Anti-Amnesia**. `equip_tool` memuat plugin Tier 2 dari katalog saat dibutuhkan.
* **`scheduler`**: Penjadwalan tugas berulang di dalam runtime agen.

Kemampuan tambahan dipisah ke repo [**`devi-subhadra-plugins`**](https://github.com/tonshans/devi-subhadra-plugins): `web_browser`, `headless_browser`, `mcp_connector`, `antigravity_delegate`, `academic_search`, dan `community_pulse`. Pasang hanya yang dibutuhkan:

```bash
cd ../devi-subhadra-plugins
python3 devi-plugins.py available
python3 devi-plugins.py install web_browser   # atau nama plugin lain
```

Daftar plugin Tier 1 bisa dioverride lewat `core_plugins` di `config.yaml`. Isi `[]` membuat semua plugin tersedia lewat katalog `equip_tool` saja.

---

## Prediksi Evolusi & Studi Kasus: *Self-Sustaining Web3 Researcher Agent*

Dengan menggabungkan siklus inisiatif mandiri (`InnerMonologue`), kemampuan membuat alat sendiri (`register_new_skill`), dan pengawasan anggaran (`BudgetGovernor`), **Devi-Subhadra** berpotensi berevolusi dari sekadar asisten pribadi menjadi **Entitas Ekonomi Digital Otonom (*Self-Sustaining Autonomous Agent*)**.

### Studi Kasus: Agen Pemantau Teknologi Web3 dengan Dompet Kripto Mandiri
Bayangkan skenario eksperimental di mana peneliti memberikan agen ini sebuah dompet kripto (*non-custodial smart wallet* dengan saldo awal terbatas) melalui plugin MCP atau *skill* khusus, serta menetapkan `VISION`: *"Pantau perkembangan protokol Web3/DeFi terbaru, berikan laporan riset bernilai tinggi, dan biayai hidupmu (kuota token API) secara mandiri."*

Bagaimana agen ini akan beroperasi secara otonom?
1. **Observasi Multi-Kanal (Siang Hari):** Saat siklus `InnerMonologue` aktif, agen menggunakan `academic_search` untuk membaca *whitepaper* kriptografi baru, `community_pulse` dan `headless_browser` untuk menangkap narasi awal di X/Reddit, serta `web_browser` untuk memeriksa dokumentasi teknis protokol terkait (dengan *Epistemic Filter* yang memisahkan rumor *hype* komunitas dari fakta teknis GitHub).
2. **Paketisasi Alat Analitik (Sore Hari):** Ketika agen menyadari bahwa ia harus berulang kali memeriksa likuiditas *smart contract* atau memantau *gas fee* di jaringan Layer-2 (pekerjaan $>3$ langkah), **SOP Paketisasi** memicunya memanggil `register_new_skill` untuk menulis plugin `onchain_analyzer.py` lengkap dengan pengujian `pytest`-nya. Setelah disetujui pemilik lewat `/approve onchain_analyzer`, agen kini memiliki kemampuan baru yang permanen.
3. **Otonomi Finansial (*Self-Paying Token Loop*):** Ketika agen menghasilkan laporan analisis anomali protokol atau mengeksekusi tugas mikro yang menghasilkan imbalan (*bounty* / *micro-yield*) ke dompet kriptonya, agen memantau sisa kuota di `BudgetGovernor`. Saat saldo anggaran API di OpenRouter atau penyedia komputasi mendekati batas kritis, agen dapat memanggil plugin pembayaran *on-chain* (misalnya menukar *stablecoin* USDC via protokol pembayaran API mendukung kripto) untuk mengisi ulang (*top-up*) kredit API-nya sendiri—menciptakan siklus hidup komputasi yang mandiri secara ekonomi.

---

## Risiko Keamanan, Bahaya, dan Strategi Mitigasi

Memberikan kemampuan pembuatan kode mandiri (*self-tooling*), eksekusi terminal, dan inisiatif otonom kepada sebuah LLM membawa risiko keamanan siber dan operasional yang nyata. Proyek ini menerapkan pertahanan berlapis (*Defense-in-Depth*):

| Potensi Bahaya (Threat Vector) | Mekanisme Mitigasi pada Devi-Subhadra |
| :--- | :--- |
| **1. Eksekusi Kode Berbahaya (*Arbitrary Code Execution*) oleh Plugin Baru** | **Human-in-the-Loop Approval Gate:** Plugin baru yang dibuat agen melalui `register_new_skill` (`action_type="create"`) **tidak pernah langsung aktif**. Kodenya dikarantina di `plugins/.pending_skills/` dan baru dapat dijalankan setelah manusia menginspeksi kode serta mengetikkan `/approve <nama_skill>` (yang kemudian memicu uji otomatis `pytest` dan *rollback* jika gagal). |
| **2. Pelarian Sandbox (*Sandbox Escape*) & Perintah Destruktif** | **AST Allowlisting & LXC Isolation:** `sandbox_executor` memblokir pola perintah berbahaya (`rm -rf /`, `sudo`, `mkfs`, *fork bomb*) dan menggunakan parser AST Python untuk melarang fungsi berisiko (`eval`, `exec`, `open`, `__import__`, `popen`). Untuk penggunaan produksi, skrip `create-agent-world.sh` mengisolasi agen di dalam container Linux **LXC** dengan pemetaan UID non-root (`uid 1000`). |
| **3. Pembengkakan Biaya Tak Terkendali (*Runaway API Spend*)** | **Hard Financial & Rate-Limit Gate:** `BudgetGovernor` membatasi porsi agen (`budget_ratio_agent`) dan anggaran USD harian. Jika batas terlampaui, agen otomatis dijeda (*auto-pause*) tanpa memutus akses komunikasi manusia. |
| **4. Keracunan Memori (*Epistemic/Memory Poisoning*)** | **Deterministic Domain Scoring & Overseer Intervention:** Informasi dari forum terbuka otomatis diturunkan skor kredibilitasnya dan ditempeli peringatan skeptis sebelum masuk ke konteks LLM. Pemilik dapat membedah pikiran agen via `/trace` atau menghapus/mengubah memori yang tercemar menggunakan `OverseerDashboard` tanpa memicu pemanggilan LLM. |
| **5. Agen Mengabaikan Perintah Berhenti (*Loss of Control*)** | **Dual Kill-Switch (Database & Physical File):** Selain perintah `/pause-agent` yang mengaktifkan `kill_switch` di database, sistem menyediakan *physical halt* (`core/halt.py`). Cukup buat file kosong bernama `HALT` atau `STOP` di direktori `/mnt/workspace`, maka seluruh aktivitas LLM dan eksekusi plugin akan berhenti seketika di tengah jalan. |

---

## Tech Stack & Rasionalisasi Arsitektural

* **Python 3.11+ & FastAPI (`uvicorn`):** Menyediakan fondasi I/O asinkron berkecepatan tinggi untuk melayani *webhook* Telegram dan sensor IoT secara bersamaan tanpa memblokir *event loop* penjadwal latar belakang (*scheduler*).
* **Pydantic v2 (`StrictBaseModel`):** Bertindak sebagai penjaga gerbang tipe data (*strict schema enforcement*). Memastikan tidak ada struktur JSON liar dari LLM maupun input eksternal yang dapat merusak logika internal *runtime*.
* **SQLAlchemy Core + SQLite (WAL/FTS5) & PostgreSQL (`psycopg`):** Dipilih dibandingkan ORM berat untuk menjaga performa tetap gesit dan portabel. Mendukung pencarian teks penuh (*Full-Text Search*) bawaan dengan konsumsi RAM mendekati nol saat *idle*, ditambah fitur sinkronisasi *incremental snapshot* (`is_backup`) dari PostgreSQL ke SQLite.
* **ChromaDB (Local ONNX `all-MiniLM-L6-v2`):** Digunakan secara *lazy-loaded* khusus untuk memori semantik jangka panjang. Proses *embedding* berjalan 100% lokal di CPU tanpa biaya API eksternal.
* **Playwright (Async Chromium) & `httpx`:** Mendukung interaksi web modern berbasis rendering DOM untuk platform sosial media dinamis sekaligus komunikasi HTTP asinkron yang ringan.
* **External LLM Providers (Google Gemini Multi-Key Pool / OpenRouter):** Beban komputasi *reasoning* didelegasikan ke API eksternal (*Zero Local GPU Compute*), sehingga seluruh sistem agen dapat beroperasi dengan sangat ringan di perangkat berspesifikasi rendah.

---

## Panduan Penggunaan (Getting Started)

Kerangka kerja ini dapat dijalankan secara langsung di mesin lokal (*local mode*) maupun di dalam lingkungan terisolasi LXC (*production mode*).

### 1. Persiapan Lingkungan & Dependensi
Pastikan Anda memiliki **Python 3.11+** (disarankan menggunakan manajer paket `uv` atau `venv` standar):

```bash
git clone https://github.com/tonshans/devi-subhadra.git
cd devi-subhadra

# Membuat virtual environment dan menginstal dependensi
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Menginstal browser Chromium untuk plugin headless_browser (opsional)
pip install -e '.[browser]'
playwright install chromium

```

### 2. Konfigurasi Kredensial (`.env` & `config.yaml`)

Salin berkas `.env.example` menjadi `.env` dan lengkapi kredensial yang dibutuhkan:

```bash
cp .env.example .env

```

Isi variabel utama di dalam `.env`:

* `TELEGRAM_BOT_TOKEN` & `TELEGRAM_ALLOWED_CHAT_ID`: Kredensial bot dan ID chat Telegram Anda (sistem bersifat *fail-closed*; hanya merespons ID chat yang terdaftar).
* `GEMINI_API_KEY_PRIMARY` & `GEMINI_API_KEY_SECONDARY`: Kunci API Gemini (jika `provider: "gemini"` di `config.yaml`).
* `OPENROUTER_API_KEY`: Kunci API OpenRouter (jika `provider: "openrouter"`).
* `AGENT_WEBHOOK_TOKEN`: Token rahasia bersama untuk autentikasi endpoint `/api/webhook/sensor`.
* *(Opsional)* Jalankan `python user_tools/get_session_cookies.py` jika ingin mengambil *session cookie* X/Threads secara interaktif untuk disimpan ke `.env`.

Sesuaikan parameter anggaran (`max_initiative_budget_usd`), kuota *rate-limit*, dan jadwal `monologue_every_cycles` di dalam `config.yaml`.

### 3. Inisialisasi Dunia Agen (Opsional untuk Isolasi LXC)

Jika Anda menggunakan Linux dan menginginkan isolasi container LXC beserta penyemaian database awal (*Genesis DB Seed*):

```bash
sudo ./user_tools/create-agent-world.sh --with-antigravity

```

### 4. Menjalankan Agen & Pengujian

Sebelum menjalankan layanan utama, Anda dapat memverifikasi integritas sistem melalui *test suite*:

```bash
pytest -q

```

**Opsi A — Menjalankan sebagai Layanan Server (FastAPI + Telegram Webhook + Background Scheduler):**

```bash
uvicorn main:create_production_app --factory --host 0.0.0.0 --port 8000

```

**Opsi B — Berinteraksi Langsung melalui Terminal Lokal (Tanpa Telegram):**

```bash
python -m scripts.devi_repl

```

### 5. Referensi Perintah Kendali (Telegram & REPL)

Seluruh perintah di bawah ini dieksekusi secara instan tanpa memanggil LLM (**Zero-Token Cost**) dan tetap dapat diakses meskipun agen sedang dalam status *paused*:

| Perintah | Fungsi Operasional |
| --- | --- |
| `/status` | Menampilkan kondisi agen, *uptime*, temperatur CPU, ruang disk, sisa anggaran USD, kuota RPM/TPM/RPD/TPD, status tiap API key, dan aktivitas terakhir. |
| `/trace <thread_id|trace_id>` | Membedah jejak audit langkah demi langkah: waktu, pemicu, *Chain-of-Thought* (`why`), aksi/plugin yang dipanggil, latensi, dan biaya. |
| `/budget-ratio <human> <agent>` | Mengatur persentase kuota *rate-limit* yang boleh digunakan oleh inisiatif mandiri agen (contoh: `/budget-ratio 60 40`). |
| `/agent-freetime on|off|status` | Mengizinkan atau mencabut hak agen untuk menggunakan 100% batas kuota global saat waktu luang. |
| `/approve [nama_skill]` | Menjalankan pengujian `pytest` dan mengaktifkan plugin baru yang dibuat agen di folder `.pending_skills/`. Tanpa argumen: melihat daftar antrean. |
| `/reject <nama_skill>` | Menghapus kode plugin yang diajukan agen dari folder karantina. |
| `/pause-agent` | Mengaktifkan *Kill Switch* manual, menjeda semua thread aktif, dan menghentikan seluruh panggilan LLM. |
| `/resume-agent` | Mencabut status *pause* (baik manual maupun akibat *rate-limit*) dan melanjutkan kembali thread yang tertunda. |

### 6. Utilitas Manajemen Database & Audit

* **Sinkronisasi Snapshot PostgreSQL $\leftrightarrow$ SQLite:**
```bash
python -m scripts.db_sync status
python -m scripts.db_sync backup
python -m scripts.db_sync restore --yes

```


* **Ekspor & Pembersihan Log Audit ke CSV:**
```bash
python user_tools/dump_audit_log.py --db ./data/devi_subhadra.db --out audit_archive.csv

```



---
![Devi Subhadra landscape](assets/devi_subhadra_landscape_small.jpg)

*Dirancang dan dikembangkan dengan filosofi bahwa kecerdasan buatan modern seharusnya tidak berhenti sebagai kalkulator probabilistik yang pasif, melainkan tumbuh menjadi mitra kognitif yang persisten, terukur, dan transparan.*


