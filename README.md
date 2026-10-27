# claudetrader

Bot paper-trading kripto yang jujur: strategi berbasis aturan, batas risiko keras di kode, Claude sebagai *reviewer* opsional (hanya boleh memveto atau mengecilkan posisi), dan dashboard ala TradingView memakai [Lightweight Charts](https://www.tradingview.com/lightweight-charts/) yang open source.

Dibangun untuk belajar dan menguji sebelum pakai uang asli. Eksekusi order sungguhan ada, tapi **nonaktif secara default** dan hanya bisa menyala kalau kamu mengetik frasa konfirmasi, memasang API key, dan mengisi batas order. Lihat bagian "Mode live".

> **Disclaimer.** Ini bukan nasihat keuangan. Riset populasi menunjukkan 74–97% trader ritel rugi (Chague dkk. 2020, Barber & Odean, ESMA). Strategi contoh di sini *rugi* di beberapa periode dan untung di periode lain, lihat tabel di bawah. Gunakan untuk belajar cara kerja sistem, bukan sebagai sumber penghasilan.

## Cara kerja

```
candle tertutup
   │
   ▼
strategy.py   EMA cross + filter trend + RSI  →  usulan BUY / SELL / HOLD
   │
   ▼
risk.py       bisa buka posisi? (daily loss, max drawdown, cooldown)
              berapa ukurannya? (1% ekuitas di stop, cap 50% posisi, cap cash)
   │
   ▼
reviewer.py   (opsional) Claude menilai setup → approve | reduce (×0.1–0.9) | veto
              tidak bisa memperbesar posisi, menggeser stop, atau membuka trade sendiri
   │
   ▼
broker.py     paper fill di harga close + slippage, fee 0,1%, PPh final saat jual
   │
   ▼
journal.jsonl + state.json + dashboard.json
```

Komponen lain: `data.py` (ccxt + cache CSV; Binance lewat mirror publik `data-api.binance.vision`, Indodax didukung), `engine.py` (pipeline yang sama untuk backtest dan paper), `metrics.py`, `paper.py` (loop live yang tahan restart), `serve.py` + `dashboard/index.html`.

## Mulai

```bash
git clone <repo> && cd <repo>
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # opsional, semua ada default

python -m claudetrader fetch --bars 3000              # unduh candle ke data/
python -m claudetrader backtest --bars 3000 --trades  # jalankan strategi, cetak metrik
python -m claudetrader serve                          # buka http://127.0.0.1:8000
python -m claudetrader paper                          # paper trading live (Ctrl-C untuk berhenti)
python -m claudetrader paper --once                   # satu putaran, cocok untuk cron
python -m pytest                                      # 43 tes
```

Panduan menjalankan di laptop atau VPS (systemd, cron, SSH tunnel): [docs/DEPLOY.md](docs/DEPLOY.md).

Semua konfigurasi lewat environment variable atau file `.env`. Lihat `.env.example` untuk daftar lengkap (exchange, pair, timeframe, fee, pajak, batas risiko, parameter indikator, mode reviewer).

### Mengaktifkan reviewer Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export CLAUDE_REVIEW=advisory        # atau required
python -m claudetrader review-test   # kirim satu usulan sintetis, cek key dan skema
python -m claudetrader paper         # tiap sinyal BUY akan direview
python -m claudetrader backtest --review   # review di backtest juga bisa, tapi memakan token per entry
```

Mode `advisory`: kalau API error, trade tetap jalan sesuai aturan. Mode `required`: kalau API error atau model menolak, trade dibatalkan (fail-safe). Model default `claude-opus-5-5`, ganti lewat `CLAUDE_MODEL`.

Reviewer menerima konteks terstruktur (harga, stop, jarak stop, ukuran relatif ekuitas, indikator, drawdown saat ini, 5 trade terakhir) dan wajib menjawab JSON `{decision, size_multiplier, confidence, reasoning}` lewat structured output. Kode memaksa multiplier ke [0, 1] apa pun jawabannya.

## Hasil backtest contoh (BTC/USDT, data Binance, 3000 candle terakhir)

Diambil 13 Okt 2026. Fee 0,1% per sisi, slippage 5 bps, PPh final saat jual sesuai PMK 50/2025: 1% untuk exchange luar negeri, 0,21% untuk exchange dalam negeri berizin OJK.

| Timeframe | Periode | Pajak jual | Return strategi | Buy & hold | Max DD | Trade | Win rate |
|---|---|---|---|---|---|---|---|
| 1h | Jun–Okt 2026 | 1,00% | **−12,1%** | +30,7% | 12,3% | 16 | 0% |
| 1h | Jun–Okt 2026 | 0,21% | **−6,4%** | +31,8% | 6,8% | 16 | 25% |
| 4h | Jun 2025–Okt 2026 | 1,00% | +2,8% | −25,8% | 7,7% | 11 | 36% |
| 4h | Jun 2025–Okt 2026 | 0,21% | +7,2% | −25,2% | 5,3% | 11 | 45% |

Tiga pelajaran yang langsung terlihat dari angka ini:

1. **Biaya menentukan.** Strategi yang sama rugi 12% dengan pajak 1% dan rugi 6% dengan pajak 0,21%. Strategi yang sering keluar-masuk di exchange luar negeri harus punya edge jauh di atas 1,2% per putaran.
2. **Timeframe lebih besar, trade lebih sedikit, biaya lebih kecil.** Di 4h strategi ini positif, tapi hanya karena periodenya berbeda (pasar turun, strategi trend-following menghindar). Jangan simpulkan 4h "lebih bagus" dari satu sampel.
3. **Buy & hold adalah pembanding yang wajib.** Di periode naik, strategi ini kalah telak dari sekadar hold.

Jalankan sendiri dengan parameter lain: `TIMEFRAME=4h TAX_SELL_RATE=0.0021 python -m claudetrader backtest --bars 3000`.

## Mode live (uang asli)

Pipeline strategi, risk, dan reviewer tidak berubah. Yang berbeda hanya siapa yang mengeksekusi: `LiveBroker` (`live_broker.py`) mengirim market order lewat ccxt, membaca harga dan fee dari fill sungguhan, dan menjadikan saldo exchange sebagai sumber kebenaran untuk kas.

Exchange yang sudah diperhitungkan:

| Exchange | Pasar | Pajak jual default | Stop di sisi exchange | Testnet |
|---|---|---|---|---|
| Indodax | lokal, izin OJK, pasangan IDR | 0,21% (dipotong exchange) | tidak ada, stop dievaluasi bot per candle | tidak ada |
| Tokocrypto | lokal, izin OJK, pasangan USDT | 0,21% (dipotong exchange) | ada (`EXCHANGE_STOP=1`) | tidak ada |
| Binance | internasional | 1% (kamu setor sendiri) | ada (`EXCHANGE_STOP=1`) | ada (`EXCHANGE_SANDBOX=1`) |

Pengaman yang berjalan di kode, bukan di prompt:

1. `EXECUTION=live` **dan** `LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY` **dan** API key harus ada. Kurang satu, bot menolak start.
2. `MAX_ORDER_NOTIONAL` membatasi nilai tiap order, `MAX_DAILY_ORDERS` membatasi jumlah order per hari, `MAX_POSITION_PCT` di atas 50% ditolak di mode live.
3. Saat start, saldo exchange direkonsiliasi dengan state lokal. Kalau beda, bot menolak jalan sampai kamu membereskannya manual.
4. Tiap candle, bot mengecek apakah posisi ditutup di luar bot (stop exchange terpicu, jual manual) dan mencatatnya.
5. File `data/live/STOP` (dibuat lewat `python -m claudetrader panic`) membuat loop menutup posisi di harga pasar lalu berhenti.
6. Notifikasi Telegram opsional untuk tiap order dan tiap error.
7. State, jurnal, dan dashboard live disimpan di `data/live/`, terpisah dari paper, dan file state paper tidak bisa dimuat sebagai live atau sebaliknya.

Urutan yang disarankan sebelum uang asli:

```bash
# 1. cek key tanpa order apa pun (pakai key dengan izin trade saja, withdrawal OFF, IP whitelist)
EXCHANGE=binance EXCHANGE_SANDBOX=1 EXCHANGE_API_KEY=... EXCHANGE_API_SECRET=... python -m claudetrader balance

# 2. testnet Binance dengan alur API yang sama persis (saldo palsu)
EXECUTION=live EXCHANGE_SANDBOX=1 LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY MAX_ORDER_NOTIONAL=50 python -m claudetrader live

# 3. uang asli, nominal terkecil yang diizinkan pair-nya, setelah paper dan testnet beres
EXECUTION=live LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY MAX_ORDER_NOTIONAL=20 MAX_DAILY_ORDERS=4 python -m claudetrader live

# tombol darurat dari terminal lain
EXECUTION=live python -m claudetrader panic
```

Indodax dan Tokocrypto tidak punya testnet, jadi langkah 2 dilewati dan langkah 3 dimulai dengan nominal minimum pair (cek lewat `balance`).

## Batas yang disengaja

- **Long-only, spot, satu pair, satu posisi.** Cukup untuk belajar pipeline. Short dan leverage tidak ada.
- **Fill di harga close candle + slippage.** Tidak ada simulasi order book.
- **Reviewer hanya dipanggil saat sinyal BUY.** Exit sepenuhnya mekanis (stop, trailing stop, cross down) supaya LLM tidak bisa "berharap" posisi rugi berbalik.
- **Market order saja.** Tidak ada limit order pasif, tidak ada eksekusi parsial yang canggih. Untuk nominal kecil di pair likuid seperti BTC ini cukup.
- **Jangan pernah beri API key izin withdrawal.** Kode ini tidak membutuhkannya, dan kalau VPS atau key bocor, uang tetap tidak bisa ditarik.

## Jalur yang disarankan

1. Jalankan backtest di beberapa timeframe dan periode. Pahami kenapa strategi menang atau kalah.
2. Jalankan `paper` minimal 3 bulan (bisa lewat cron: `*/5 * * * * cd /path && python -m claudetrader paper --once`). Bandingkan dengan buy & hold setelah biaya.
3. Ubah strategi di `strategy.py`, tambah tes di `tests/`, ulangi.
4. Baru pertimbangkan uang asli, dengan nominal yang rela hilang seluruhnya.

## Lisensi

MIT. Lightweight Charts™ oleh TradingView, Apache 2.0, atribusi ada di footer dashboard.
