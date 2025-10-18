# Telegram Markdown to PDF Bot

Bot Telegram yang mengkonversi teks Markdown menjadi file PDF dengan styling yang rapi.

## 🚀 Fitur

- Konversi Markdown ke PDF dengan styling profesional
- Support untuk:
  - Heading (H1-H6)
  - Bold, Italic, Code
  - List (ordered & unordered)
  - Tables
  - Blockquotes
  - Code blocks
- Resource limits untuk efisiensi
- Auto-restart jika terjadi error

## 📋 Prasyarat

- Docker dan Docker Compose terinstall
- Token bot dari [@BotFather](https://t.me/botfather) di Telegram

## 🛠️ Cara Membuat Bot Telegram

1. Buka Telegram dan cari **@BotFather**
2. Kirim command `/newbot`
3. Ikuti instruksi untuk memberi nama bot Anda
4. Copy token yang diberikan

## 📦 Instalasi

1. Clone atau download project ini

2. Buat file `.env` dari template:

```bash
cp .env.example .env
```

3. Edit file `.env` dan masukkan token bot Anda:

```
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

4. Build dan jalankan dengan Docker Compose:

```bash
docker-compose up -d --build
```

5. Cek logs untuk memastikan bot berjalan:

```bash
docker-compose logs -f
```

## 📱 Cara Menggunakan Bot

1. Buka bot Anda di Telegram
2. Kirim command `/start`
3. Kirim teks Markdown Anda (bisa dalam beberapa pesan terpisah)
4. Gunakan `/convert` untuk mengkonversi semua pesan menjadi PDF
5. Bot akan mengirimkan file PDF hasil konversi

**Tips:** Jika pesan Anda panjang dan terpotong menjadi beberapa bubble, tidak masalah! Bot akan menggabungkan semua pesan yang Anda kirim sebelum mengkonversi ke PDF

### Contoh Markdown:

````markdown
# Laporan Bulanan

## Ringkasan Eksekutif

Ini adalah **laporan penting** untuk bulan ini.

### Highlights:

- Peningkatan 20% dalam penjualan
- Peluncuran 3 produk baru
- Ekspansi ke 2 kota baru

## Data Penjualan

| Produk | Unit Terjual | Revenue |
| ------ | ------------ | ------- |
| A      | 100          | $1000   |
| B      | 150          | $1500   |

> "Kesuksesan adalah hasil dari persiapan yang baik"

### Kode Contoh:

```python
def hello():
    print("Hello World")
    return True
```
````

Atau gunakan inline code seperti `print("test")` di dalam teks.

````

## 🎛️ Resource Limits

Bot dikonfigurasi dengan resource limits berikut:

- **CPU**: Max 50%, Min 25%
- **Memory**: Max 512MB, Min 256MB
- **Logs**: Max 10MB per file, max 3 files

## 📝 Commands

- `/start` - Mulai bot dan siap menerima Markdown
- `/convert` - Konversi semua markdown yang sudah dikirim menjadi PDF
- `/status` - Cek berapa banyak pesan markdown yang sudah dikirim
- `/cancel` - Batalkan proses dan hapus semua markdown

## 🔧 Mengelola Bot

### Melihat logs:
```bash
docker-compose logs -f telegram-bot
````

### Restart bot:

```bash
docker-compose restart
```

### Stop bot:

```bash
docker-compose down
```

### Update bot:

```bash
docker-compose down
docker-compose up -d --build
```

## 📂 Struktur File

```
.
├── bot.py                 # Kode utama bot
├── Dockerfile            # Docker configuration
├── docker-compose.yml    # Docker Compose configuration
├── requirements.txt      # Python dependencies
├── .env.example         # Template environment variables
└── README.md            # Dokumentasi
```

## 🐛 Troubleshooting

### Bot tidak merespon:

- Pastikan token di `.env` benar
- Cek logs: `docker-compose logs -f`
- Restart bot: `docker-compose restart`

### Error saat build:

- Pastikan Docker dan Docker Compose terinstall dengan benar
- Coba hapus image lama: `docker-compose down --rmi all`
- Build ulang: `docker-compose up -d --build`

### PDF tidak ter-generate:

- Cek format Markdown Anda
- Lihat error di logs
- Pastikan memory cukup

## 📄 License

MIT License - Bebas digunakan untuk keperluan apapun.

## 🤝 Kontribusi

Silakan buat issue atau pull request untuk improvement!
