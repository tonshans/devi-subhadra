![Devi Subhadra](assets/devi_subhadra_2d_vector_small.jpg)

# Devi-Subhadra: Autonomous Cognitive Agent Framework

> *"Building a Digital Coworker with Persistence, Continuous Learning, and Regulated Creativity."*

## Sang Devi Subhadra
Dalam bentangan epos Mahabharata, sosok Devi Subhadra sering kali dipandang melalui lensa sekunder di tengah hiruk-pikuk epik para ksatria besar. Namun, dalam kajian karakter yang lebih mendalam, Subhadra adalah manifestasi murni dari titik keseimbangan dan energi feminin yang subtil namun krusial. Ia merepresentasikan **persistensi** (ketahanan dan kegigihan) yang tak pernah redup di tengah pusaran konflik, serta **semangat pembelajar yang berkesinambungan** (adaptasi dan kerendahan hati) untuk bertahan dan memahami lanskap yang terus berubah. 

Lebih jauh, Subhadra sangat erat kaitannya dengan daya cipta kreatif, layaknya personifikasi parsial dari *Yogamaya*—kekuatan ilahi yang mampu menenun dan merajut realitas baru. Karakteristik inilah yang menjadi landasan ontologis di balik penamaan proyek ini. *Devi-Subhadra* bukan sekadar sekumpulan baris kode otomatisasi; ia adalah eksperimen untuk menginjeksikan ketahanan logika, kapabilitas adaptasi mandiri, dan otonomi berkreasi ke dalam wujud kecerdasan buatan (*Artificial Intelligence*).

---

## Tujuan Project
Gagasan kerangka kerja ini bermula dari sebuah pertanyaan spekulatif: *"Bagaimana jika sebuah agen AI tidak hanya terdiam menunggu rentetan instruksi, melainkan dibiarkan belajar, berinisiatif, dan berkarya secara otonom di waktu luangnya?"*

Hingga saat ini, ekosistem agen AI terlalu berfokus pada paradigma reaktif. Proyek ini bertujuan meruntuhkan paradigma tersebut dengan membangun sebuah **"Rekan Kerja Digital" (Autonomous Cognitive Agent)** yang proaktif. Dalam project ini berupaya membangun agen yang bisa terus belajar memperkaya ruang memori internalnya dengan informasi dunia nyata seiring berjalannya waktu dan juga memiliki kemampuan untuk berekspresi melalui karya yang keluar dari ide dan hasratnya sendiri, namun harus **tetap sangat ringan (lightweight), mudah terdistribusi, dan mudah dipelihara (maintainable)**. 

Hasilnya adalah terwujudnya arsitektur **The Glass-Box AGI Microcosm**. Sebuah sistem agen yang cerdas dan hidup, namun operasionalnya 100% transparan, terukur secara ekonomi, dapat diaudit penuh tanpa mengkonsumsi biaya *token API*, dan sepenuhnya terlepas dari ikatan antarmuka atau sistem operasi tertentu (*OS-Agnostic*).

---

## Empat Pilar Filosofis
Arsitektur dan setiap keputusan rekayasa perangkat lunak (*software engineering*) pada proyek ini selalu diuji terhadap empat pilar berikut:

1. **Cognitive Realism (Realisme Kognitif)**
   Sistem ini dirancang menyerupai ritme sirkadian biologis. Agen bekerja mendelegasikan tugas pada siang hari, melakukan refleksi (*Inner Monologue*) saat waktu luang, dan mengonsolidasikan log hariannya menjadi memori permanen (*Semantic Memory*) di fase malam (*Sleep Mode*). Dalam project ini juga mengimplementasikan mekanisme *Semantic Decay*, di mana agen didesain untuk "lupa" terhadap aturan atau preferensi yang tak lagi dipakai lebih dari 30 hari.
   
2. **Fail-Gracefully, Heal-Silently (Sistem Imun Mandiri)**
   Sistem tidak boleh mengganggu penggunanya dengan error sintaksis trivial. Melalui modul *Healer Node*, agen mengevaluasi sendiri kegagalan eksekusinya dan mencoba memperbaiki dirinya sendiri secara terisolasi hingga maksimal 3 kali (termasuk validasi kebenaran melalui *Assertion-Driven check*). 
   
3. **Radical Portability (Eksistensi Nomaden)**
   Tubuh (*Sandbox*) dan memori agen tidak boleh terbelenggu pada satu infrastruktur fisik atau *Cloud*. Menggunakan *Zero-Latency Bind Mounts* dan database relasional berorientasi portabilitas tinggi, seluruh "pikiran dan tubuh" agen dapat di-*copy* dan dijalankan pada PC Windows, Linux Server, hingga Raspberry Pi dalam hitungan detik tanpa amnesia.
   
4. **Governed Creativity (Kreativitas Terukur & Dianggarkan)**
   Otonomi untuk berkreasi (berinisiatif) adalah esensi proyek ini, namun komputasi menuntut biaya ekonomi. Proaktivitas agen secara mutlak diawasi oleh *The Budget Governor*, di mana inisiatif mandiri agen dialokasikan misalnya secara statis: 70% *budget* harian untuk tugas inti/optimasi, dan 30% murni untuk eksperimen kreatif (Daya Cipta).

---

## Pemilahan Tegas: Core Engine vs Default Extensions
Untuk menjamin prinsip skalabilitas, *codebase* ini memberlakukan restriksi radikal:

* **The Immutable Core (Otak & Saraf)**: Terdiri atas modul manajer memori, pengatur jadwal (*scheduler*), *router* model kognitif, dan gerbang anggaran. Bagian ini dikunci ketat. Dilarang keras melakukan manipulasi teks bebas (*free-text parsing*); segala pengambilan keputusan diregulasi melalui validasi JSON/Schema statis agar sistem kebal dari "halusinasi operasional".
* **The Extensible Peripherals (Tangan & Sensor)**: Seluruh fungsi yang menyentuh dunia eksternal (pembaca file, pengeksekusi Python/Bash lokal, peramban web, hingga konektor standar *Model Context Protocol*/MCP) diimplementasikan sebagai sistem *Plugin* (Ekstensi). Jika seluruh ekstensi ini dicabut atau mengalami malfungsi, agen tidak akan "mati"—ia tetap hidup, bisa berkomunikasi, dan merenungkan nasib sistemnya.

---

## Spesifikasi Fitur Kognisi, Memori, & Prioritas
Memori pada *Devi-Subhadra* bukan menggunakan satu ruang penyimpanan data (*Vector DB*) raksasa yang rawan mengalami *context bloat* (ledakan token). Memori ditata secara kaku dalam hierarki SQL terstruktur:

- **DNA & Semantic Memory**: Perintah inti dan karakter absolut yang diset pengguna, dipadukan dengan heuristik operasional (kebijaksanaan) yang dipelajari dan diseleksi secara dinamis oleh agen seiring berjalannya waktu.
- **Ledger & Atelier Discoveries**: Agen dipisahkan ruang berpikirnya antara "Memori Kerja" (*Ledger* misi saat ini) dengan "Perpustakaan Pribadi" (*Atelier* tempat ia mencatat fakta menarik dari internet) agar memori tugas operasional tidak tercemar oleh bacaan eksploratifnya.
- **Sistem Prioritas (The Endorsement Override)**: Meskipun agen aktif memprakarsai idenya sendiri (*Priority Score: Low*), instruksi manusia adalah otoritas tertinggi (*Priority Score: High*). Namun, sistem memperbolehkan pengguna untuk melakukan *Endorsement* (Validasi Intervensi) terhadap misi kreatif agen, yang secara dinamis meningkatkan skor misi agen tersebut, sekaligus membiayainya dari anggaran tugas utama.

---

## Tech Stack & Rasionalisasi
* **Python (FastAPI & APScheduler)**: Fundamental untuk melayani asinkronisasi *Webhook* yang berkecepatan tinggi tanpa memblokir ritme *background processing* untuk pemikiran mandiri (*Inner Monologue*).
* **PydanticAI**: *Framework* krusial pemaksa tipe (type-enforcer). Memaksa Output LLM (Large Language Model) agar 100% mematuhi struktur data terikat untuk mencegah keruntuhan logika *agentic loop*.
* **SQLAlchemy Core + SQLite / PostgreSQL**: Manajemen state dilakukan secara transaksional murni via SQL (SQLite FTS5 atau PostgreSQL `tsvector`) alih-alih Vector Database, sehingga konsumsi RAM mendekati nol pada keadaan *idle*, ideal untuk *OS-Agnostic portability*. `db_type: postgres` memakai PostgreSQL sebagai database utama; snapshot SQLite periodik menjadi sumber restore saat agen dipindahkan.
* **LXC / RestrictedPython**: Mode isolasi untuk *sandbox execution*. Menjamin agen bisa melakukan eksperimen baris kodenya sendiri tanpa mengundang bencana pada mesin induk (*host*).
* **API Providers (Gemini / OpenRouter)**: Proses komputasi *reasoning* sengaja dibuang ke eksternal untuk memungkinkan *Zero Local Compute* saat inferensi, mempertahankan karakter "ringan" (lightweight).

---

## Cara Penggunaan (Getting Started)
Framework ini dirancang untuk dijalankan layaknya skrip *background service* (*Headless*).

1. **Persiapan Lingkungan**: Klon repositori ini. Buatlah virtual environment lokal (*venv*), instal rincian dependensi, dan pastikan LXC (jika memilih isolasi ketat) telah beroperasi di *host* Anda.
2. **Kredensial & Konfigurasi**: Salin `config.yaml` dan sediakan *API Key* penyedia LLM Anda (via `.env`). Tentukan rasio batas keuangan (Token Budget) harian secara sadar.
3. **Eksekusi Skrip Genesis**: Jalankan skrip `create-agent-world.sh`. Skrip ini akan melakukan *mounting* volume, menyuntikkan *DNA (System Prompts)* ke dalam SQL, dan membangkitkan struktur kesadaran awal.
4. **Interaksi & Audit**: Nyalakan `main.py`. Agen akan merespons di kanal Telegram yang Anda berikan. Anda bebas mengarahkan tugas kepadanya, atau biarkan ia belajar dan membuat *script* utilitas mandiri. Anda sebagai sang Arsitek bisa mengakses **The Overseer Dashboard** via CLI/Web-Proxy kapan saja untuk memonitor pikiran (*Live Cortex*), laju anggaran, dan perkembangan sifat agen tanpa ketahuan (Zero-Token Cost).

### Menjalankan Service

```bash
cp .env.example .env   # isi token & API key
.venv/bin/python -m pytest -q
.venv/bin/uvicorn main:create_production_app --factory
```

- Wajib: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, plus secret provider. `provider: gemini` membaca `GEMINI_API_KEY_PRIMARY` (dan `GEMINI_API_KEY_SECONDARY` bila dikonfigurasi); `provider: openrouter` membaca `OPENROUTER_API_KEY`. Opsional: `AGENT_WEBHOOK_TOKEN`.
- Semua unit test berada di `tests/` (satu file `<nama_modul>.test.py` per modul); `uv run pytest -q` hanya menjalankan folder itu.
- Isi `pricing.input_usd_per_million` dan `pricing.output_usd_per_million` di `config.yaml` agar anggaran memotong biaya token nyata. Nilai `0.0` mempertahankan biaya datar `cost_usd`.
- Kill-switch menjeda semua thread `Active` dan menolak misi baru dengan HTTP 503; `resume()` membuka kembali thread tersebut.
- `GET /api/health` melaporkan `database`, `db_backend` (`sqlite`/`postgres`/`sqlite-fallback`), `db_fallback`, `kill_switch_active`, `provider`, dan `provider_keys_ready`.
- Endpoint sensor memakai `origin='AGENT'` (prioritas 10) agar tidak melewati anggaran atau mengalahkan misi manusia.
- Sandbox saat ini subprocess + allowlist AST, bukan LXC. Jangan jalankan kode tak tepercaya sampai isolasi host tersedia.

### Command Telegram & REPL

Command yang sama tersedia lewat bot Telegram dan REPL lokal `python -m scripts.devi_repl`. Command selalu bisa dipanggil walau agent sedang pause.

| Command | Keterangan |
| --- | --- |
| `/status` | Snapshot agent, uptime, anggaran USD, limit provider global (RPM/TPM/RPD/TPD), status tiap API key, pemakaian sisi agent, rasio human/agent, status freetime, alasan pause, dan aktivitas terakhir. Tidak memanggil LLM. |
| `/budget-ratio` | Menampilkan rasio human/agent, porsi agent untuk tiap limit, dan status freetime. |
| `/budget-ratio <human> <agent>` | Mengubah rasio, misalnya `/budget-ratio 60 40`. Dua bilangan bulat 0-100 yang jumlahnya 100. Porsi human adalah jatah minimum yang dilindungi; agent dibatasi `agent% × limit`. Disimpan di `system_state` dan bertahan setelah restart. |
| `/agent-freetime on` | Menonaktifkan budget ratio: inisiatif agent boleh memakai seluruh limit global. Limit global tetap berlaku. |
| `/agent-freetime off` | Mengaktifkan kembali budget ratio untuk inisiatif agent. |
| `/agent-freetime status` | Menampilkan status freetime (`on`/`off`). Tanpa argumen sama dengan `status`. |
| `/pause-agent` | Pause manual: kill switch aktif, semua thread `Active` dipause, misi baru ditolak sampai `/resume-agent`. Pause manual tidak auto-resume. |
| `/resume-agent` | Membatalkan pause manual maupun pause rate-limit, membuka kembali thread yang dipause. |
| `/trace <thread_id\|trace_id>` | Menampilkan audit trail per langkah: waktu, trigger, aksi, outcome, latency, biaya, CoT (`why`), kutipan respons, dan lokasi hasil disimpan (`where`). Tidak memanggil LLM. |
| `/approve [skill]` | Mengaktifkan plugin yang baru dibuat agent lewat `register_new_skill` (menjalankan test-nya lebih dulu). Tanpa argumen: daftar skill yang menunggu persetujuan. |
| `/reject <skill>` | Membuang kode plugin yang menunggu persetujuan tanpa mengaktifkannya. |
| `/quit`, `/exit` | Keluar dari REPL lokal (Ctrl-D juga bisa). Hanya tersedia di REPL, bukan Telegram. |

### Skill Baru dari Pekerjaan Berulang

Kalau satu misi butuh lebih dari 3 langkah dan kemungkinan diulang, agent diinstruksikan membungkusnya jadi satu plugin (`register_new_skill`, `action_type='create'`) beserta test-nya, bukan script lepas. Plugin baru **tidak langsung aktif**: kodenya ditahan di `plugins/.pending_skills/` sampai pemilik menjalankan `/approve <nama>`. Setelah disetujui, test dijalankan, plugin didaftarkan ke `plugin_registry` tier `ATELIER`, dan muncul di katalog tool pada misi berikutnya tanpa restart. `upgrade` dan `restore` tetap otonom.

Alasannya: kode plugin berjalan di proses agent dengan akses penuh ke filesystem, DB, dan API key, jadi pembuatan pertama butuh persetujuan manusia.

### Limit Provider & Budget Ratio

`rate_limits` di `config.yaml` mengatur kuota global provider; `0` berarti unlimited:

```yaml
rate_limits:
  rpm_limit: 60          # request per menit
  tpm_limit: 120000      # token per menit
  rpd_limit: 1000        # request per hari (UTC)
  tpd_limit: 2000000     # token per hari (UTC)
  budget_ratio_agent: 0.40   # porsi maksimum inisiatif agent
  agent_freetime: false
```

- Setiap percobaan worker dihitung satu request; retry healer ikut dihitung. Token diambil dari `usage` respons provider.
- Limit global tercapai: semua aktivitas LLM pause (kill switch), chat menerima alasan dan waktu auto-resume.
- Porsi agent habis: hanya thread `origin='AGENT'` yang pause; chat human tetap jalan. Pause ini bisa dilewati dengan `/agent-freetime on`.
- Auto-resume RPM/TPM di awal menit berikutnya, RPD/TPD pada 00:00 UTC hari berikutnya. Pause manual `/pause-agent` tetap menunggu `/resume-agent`.
- `budget_ratio_agent: 0` memblokir semua inisiatif agent (bukan unlimited). Nilai ratio/freetime yang diubah lewat command disimpan di `system_state` dan menimpa `config.yaml` saat startup.
- Pemakaian menit/hari disimpan di tabel `rate_limit_usage` sehingga bertahan setelah restart; snapshot lama tanpa tabel tersebut tetap bisa direstore.

### Jadwal InnerMonologue

Scheduler berjalan tiap 60 detik. `monologue_every_cycles` menentukan berapa tick sebelum satu siklus InnerMonologue dijalankan; nilai `5` berarti tiap 5 menit. Cek budget, pause, dan wake-up tetap tiap tick.

```yaml
monologue_every_cycles: 5
```

### Multi-key Gemini (Failover)

Provider Gemini mencoba key berurutan, key pertama (utama) didahulukan. Secret tetap di `.env`; `config.yaml` hanya menyimpan id, nama env, dan limit per key:

```yaml
rate_limits:
  provider: "gemini"     # "gemini" memakai gemini_keys di bawah
gemini_keys:
  - id: "primary"
    env: "GEMINI_API_KEY_PRIMARY"
    rpm_limit: 15          # 0 = unlimited
    tpm_limit: 1000000
    rpd_limit: 1500
    tpd_limit: 10000000
  - id: "secondary"
    env: "GEMINI_API_KEY_SECONDARY"
    rpm_limit: 15
    tpm_limit: 1000000
    rpd_limit: 1500
    tpd_limit: 10000000
```

- Limit per key adalah batas utama; `rate_limits` global tetap menjadi batas luar opsional (`0` = unlimited).
- Failover pada 429, 401/403, 5xx, dan timeout/jaringan. 429 cooldown mengikuti `Retry-After` atau sampai menit berikutnya; 5xx/jaringan 60 detik; 401/403 mematikan key sampai restart.
- Error request lain (mis. 400) tidak dirotasi: langsung gagal agar bug tidak tersembunyi.
- Semua key down: satu notifikasi Telegram per episode, balasan misi berisi alasan dan waktu retry otomatis, tanpa kill switch.
- `/status` menampilkan `ready`, `cooldown until ...`, atau `disabled` per key; key dengan limit lokal habis ikut ditandai.
- Pemakaian per key disimpan di tabel `provider_key_usage`; cooldown/disabled hanya di memori. Key tanpa secret dilewati saat startup, dan startup gagal bila tidak ada key tersedia.
- Nama model memakai format native Gemini, mis. `gemini-2.5-flash` (bukan `google/...`).

### PostgreSQL + Backup SQLite

- Set `db_type: "postgres"` di `config.yaml`, lalu isi `POSTGRES_DSN=postgresql+psycopg://...` di `.env`. DSN tidak pernah disimpan di `config.yaml`.
- Jika PostgreSQL tidak bisa dihubungi saat startup dan `fallback_sqlite: true`, agen berjalan sementara di `fallback_sqlite_path`; health menampilkan `db_backend: "sqlite-fallback"`. Tidak ada hot-failover saat runtime.
- Backup periodik hanya menyalin baris dengan `is_backup = false` ke satu snapshot SQLite (`backup.sqlite_path`), lalu menandainya sudah dibackup. Timestamp dibuat aplikasi agar urutan tetap deterministik di kedua backend.
- Batasan v1: penghapusan baris di PostgreSQL tidak menghapus baris di snapshot, jadi restore akan mengembalikan baris yang sudah dihapus. Restore selalu menang; jangan pakai snapshot sebagai pengganti riwayat audit.
- Restore selalu mengganti seluruh isi PostgreSQL dengan snapshot SQLite (snapshot = sumber kebenaran):

```bash
python -m scripts.db_sync status
python -m scripts.db_sync backup
python -m scripts.db_sync restore --yes
```

- Database SQLite lama: hentikan agen, lalu jalankan `python user_tools/migrate-is-backup.py --db ./data/devi_subhadra.db` (idempoten, otomatis membuat `.bak`). Untuk skema PostgreSQL lama, pakai `--dsn "$POSTGRES_DSN"` setelah `pg_dump` sendiri.

---
![Devi Subhadra landscape](assets/devi_subhadra_landscape_small.jpg)
*Dibangun dengan dedikasi di bawah filosofi bahwa sistem AI modern tidak seharusnya bertindak hanya sebagai kalkulator yang dipercantik, melainkan sebuah mitra sintetik yang persisten dan terus berproses.*
