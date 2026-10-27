# Menjalankan claudetrader di laptop atau VPS

## Laptop (paper trading)

```bash
git clone https://github.com/sanadunt/json_test && cd json_test
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # atur EXCHANGE, SYMBOL, TIMEFRAME, pajak
python -m claudetrader backtest --bars 3000 --trades
python -m claudetrader paper      # terminal 1: bot, bangun tiap candle tutup
python -m claudetrader serve      # terminal 2: dashboard di http://127.0.0.1:8000
```

Laptop harus menyala terus. Kalau sempat tidur, bot memproses candle yang terlewat secara berurutan saat bangun lagi; keputusannya terlambat tapi datanya tetap konsisten. Untuk simulasi berbulan-bulan, VPS lebih cocok.

## VPS

Spesifikasi terkecil cukup: 1 vCPU, 1 GB RAM, Ubuntu 22.04 atau lebih baru. Pilih lokasi Singapura atau Tokyo supaya dekat dengan server Binance; untuk Indodax/Tokocrypto lokasi Jakarta atau Singapura.

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/sanadunt/json_test ~/claudetrader && cd ~/claudetrader
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env && chmod 600 .env
.venv/bin/python -m claudetrader backtest --bars 1000   # cek data bisa diambil dari VPS
```

### Service systemd (bot jalan terus, hidup lagi setelah reboot)

Simpan sebagai `/etc/systemd/system/claudetrader.service` (ganti `ubuntu` dengan user kamu):

```ini
[Unit]
Description=claudetrader loop
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/claudetrader
ExecStart=/home/ubuntu/claudetrader/.venv/bin/python -m claudetrader paper
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Untuk mode live ganti `paper` menjadi `live` dan pastikan `.env` berisi `EXECUTION=live`, `LIVE_CONFIRM`, dan API key.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now claudetrader
journalctl -u claudetrader -f          # log
sudo systemctl stop claudetrader       # berhenti
```

Buat service kedua, misalnya `claudetrader-dashboard.service`, dengan `ExecStart=... -m claudetrader serve`.

### Alternatif: cron

Cukup untuk timeframe 1 jam ke atas:

```
*/5 * * * * cd /home/ubuntu/claudetrader && .venv/bin/python -m claudetrader paper --once >> data/cron.log 2>&1
```

### Melihat dashboard dari laptop

Dashboard hanya mendengarkan di 127.0.0.1, tidak ada port yang terbuka ke internet. Dari laptop:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@IP_VPS
```

lalu buka http://localhost:8000. Untuk dashboard live: jalankan `serve` dengan `EXECUTION=live` (atau `DATA_DIR=data/live`).

## Checklist sebelum mode live

1. Paper trading minimal 3 bulan, hasilnya dibandingkan dengan buy & hold setelah biaya dan pajak.
2. API key baru khusus bot: izin **trade saja**, izin withdrawal **mati**, IP whitelist ke IP VPS.
3. `.env` dengan `chmod 600`, tidak pernah masuk git.
4. `python -m claudetrader balance` berhasil dan menunjukkan minimum order pair.
5. Binance: beberapa minggu di testnet (`EXCHANGE_SANDBOX=1`). Indodax/Tokocrypto: mulai dengan nominal minimum.
6. `MAX_ORDER_NOTIONAL` dan `MAX_DAILY_ORDERS` diisi kecil. Telegram diisi supaya tiap order dan error sampai ke HP.
7. Tahu cara berhenti: `python -m claudetrader panic` (tutup posisi, lalu halt) atau `sudo systemctl stop claudetrader`.

## Memperbarui kode di VPS

```bash
cd ~/claudetrader && git pull && .venv/bin/pip install -r requirements.txt
sudo systemctl restart claudetrader
```

State di `data/` tidak tersentuh oleh `git pull`.
