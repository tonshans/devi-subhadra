![Devi Subhadra](assets/devi_subhadra_2d_vector.jpeg)

# Devi-Subhadra: Autonomous Cognitive Agent Framework

> *"Building a Digital Coworker with Persistence, Continuous Learning, and Regulated Creativity."*

## Epilog Filosofis: Sang Devi Subhadra
Dalam bentangan epos Mahabharata, sosok Devi Subhadra sering kali dipandang melalui lensa sekunder di tengah hiruk-pikuk epik para ksatria besar. Namun, dalam kajian karakter yang lebih mendalam, Subhadra adalah manifestasi murni dari titik keseimbangan dan energi feminin yang subtil namun krusial. Ia merepresentasikan **persistensi** (ketahanan dan kegigihan) yang tak pernah redup di tengah pusaran konflik, serta **semangat pembelajar yang berkesinambungan** (adaptasi dan kerendahan hati) untuk bertahan dan memahami lanskap yang terus berubah. 

Lebih jauh, Subhadra sangat erat kaitannya dengan daya cipta kreatif, layaknya personifikasi parsial dari *Yogamaya*—kekuatan ilahi yang mampu menenun dan merajut realitas baru. Karakteristik inilah yang menjadi landasan ontologis di balik penamaan proyek ini. *Devi-Subhadra* bukan sekadar sekumpulan baris kode otomatisasi; ia adalah eksperimen untuk menginjeksikan ketahanan logika, kapabilitas adaptasi mandiri, dan otonomi berkreasi ke dalam wujud kecerdasan buatan (*Artificial Intelligence*).

---

## Tujuan Project
Gagasan kerangka kerja ini bermula dari sebuah pertanyaan spekulatif: *"Bagaimana jika sebuah agen AI tidak hanya terdiam menunggu rentetan instruksi, melainkan dibiarkan belajar, berinisiatif, dan berkarya secara otonom di waktu luangnya?"*

Hingga saat ini, ekosistem agen AI terlalu berfokus pada paradigma reaktif. Proyek ini bertujuan meruntuhkan paradigma tersebut dengan membangun sebuah **"Rekan Kerja Digital" (Autonomous Cognitive Agent)** yang proaktif. Dalam project ini berupaya membangun agen yang bisa terus belajar dan memperkaya ruang memori internalnya seiring berjalannya waktu, namun harus **tetap sangat ringan (lightweight), terdistribusi, dan mudah dipelihara (maintainable)**. 

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
* **SQLite (WAL Mode) / PostgreSQL**: Manajemen state dilakukan secara transaksional murni via SQL (*Full-Text Search/FTS5*) alih-alih Vector Database, sehingga konsumsi RAM mendekati nol pada keadaan *idle*, ideal untuk *OS-Agnostic portability*.
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

- Wajib: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `OPENROUTER_API_KEY`. Opsional: `AGENT_WEBHOOK_TOKEN`.
- Isi `pricing.input_usd_per_million` dan `pricing.output_usd_per_million` di `config.yaml` agar anggaran memotong biaya token nyata. Nilai `0.0` mempertahankan biaya datar `cost_usd`.
- Kill-switch menjeda semua thread `Active` dan menolak misi baru dengan HTTP 503; `resume()` membuka kembali thread tersebut.
- `GET /api/health` melaporkan `database` dan `kill_switch_active`.
- Endpoint sensor memakai `origin='AGENT'` (prioritas 10) agar tidak melewati anggaran atau mengalahkan misi manusia.
- Sandbox saat ini subprocess + allowlist AST, bukan LXC. Jangan jalankan kode tak tepercaya sampai isolasi host tersedia.

---
![Devi Subhadra landscape](assets/devi_subhadra_landscape.jpeg)
*Dibangun dengan dedikasi di bawah filosofi bahwa sistem AI modern tidak seharusnya bertindak hanya sebagai kalkulator yang dipercantik, melainkan sebuah mitra sintetik yang persisten dan terus berproses.*
