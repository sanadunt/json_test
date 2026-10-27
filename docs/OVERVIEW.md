# claudetrader: ini proyek apa?

## Satu paragraf

claudetrader adalah bot trading kripto yang dibuat untuk **belajar dan menguji dengan aman sebelum memakai uang asli**. Bot ini membaca data pasar sungguhan, memutuskan beli/jual berdasarkan aturan yang jelas, membatasi risiko lewat kode yang tidak bisa dilanggar siapa pun (termasuk AI), dan mencatat setiap keputusannya. Claude bisa dipasang sebagai "reviewer" yang hanya boleh membatalkan atau mengecilkan trade, bukan memperbesar. Secara default bot hanya bersimulasi (paper trading). Eksekusi order sungguhan ada, tapi dikunci di balik beberapa pengaman dan harus dinyalakan secara sadar.

## Kenapa dibuat seperti ini

Riset populasi trader ritel menunjukkan 74–97% rugi (Brasil 2020, Taiwan, pengungkapan broker CFD di Eropa). Uji coba LLM trading uang asli (Alpha Arena 2025, HKU 2026) menunjukkan model AI bukan sumber keunggulan: beberapa untung, beberapa rugi besar, dan hasilnya sulit dibedakan dari acak. Dua fakta itu membentuk desainnya:

- **Edge harus datang dari strategi dan disiplin risiko, bukan dari AI.** Jadi strategi ditulis sebagai aturan yang bisa dibacktest, dan AI hanya jadi filter tambahan.
- **Biaya menentukan hasil.** Fee, slippage, dan pajak (PPh final 0,21% di exchange lokal, 1% di exchange luar negeri) dihitung di simulasi. Strategi contoh yang sama rugi 12% dengan pajak 1% dan rugi 6% dengan pajak 0,21% di periode yang sama.
- **Simulasi dulu, uang asli nanti.** Backtest, lalu paper trading berbulan-bulan, lalu testnet, baru nominal kecil.

## Apa yang dilakukan bot tiap jam

Setiap candle (default 1 jam) tutup, bot menjalankan urutan ini:

1. **Strategi** (`strategy.py`) melihat indikator: EMA 20/50/200, RSI, ATR. Kalau EMA cepat memotong ke atas EMA lambat, harga di atas EMA tren, dan RSI belum jenuh beli, strategi mengusulkan BUY dengan stop loss sejauh 2×ATR. Posisi ditutup kalau stop tersentuh (termasuk trailing stop 3×ATR) atau EMA memotong ke bawah.
2. **Risk manager** (`risk.py`) memeriksa apakah boleh buka posisi: belum menyentuh batas rugi harian 3%, belum menyentuh drawdown maksimum 15% (kalau tersentuh, bot berhenti permanen sampai direset manual), tidak sedang cooldown setelah rugi. Lalu menghitung ukuran posisi supaya kalau stop tersentuh, kerugiannya 1% dari ekuitas, dengan batas maksimum 50% ekuitas per posisi.
3. **Reviewer Claude** (`reviewer.py`, opsional) menerima ringkasan setup dan menjawab `approve`, `reduce` (kecilkan 10–90%), atau `veto`. Kode memaksa jawabannya ke rentang itu. Kalau API error: mode `advisory` tetap jalan sesuai aturan, mode `required` membatalkan trade.
4. **Broker** mengeksekusi. Di mode paper (`broker.py`) ini simulasi dengan fee 0,1%, slippage 5 bps, dan pajak jual. Di mode live (`live_broker.py`) ini market order sungguhan lewat ccxt.
5. **Jurnal dan dashboard**: setiap keputusan, termasuk HOLD dan alasannya, ditulis ke `journal.jsonl`; dashboard web menampilkan candle, EMA, penanda beli/jual, kurva ekuitas, daftar trade, dan keputusan terakhir.

## Tiga mode pakai

| Mode | Perintah | Uang | Untuk apa |
|---|---|---|---|
| Backtest | `python -m claudetrader backtest` | tidak ada | Menguji strategi di data historis, membandingkan dengan buy & hold |
| Paper | `python -m claudetrader paper` | virtual | Simulasi live di data pasar sungguhan, berminggu-minggu |
| Live | `python -m claudetrader live` | asli | Hanya setelah paper dan testnet, dengan nominal kecil |

Dashboard: `python -m claudetrader serve` lalu buka http://127.0.0.1:8000.

## Exchange yang didukung

| Exchange | Jenis | Pajak jual | Stop di sisi exchange | Testnet |
|---|---|---|---|---|
| Indodax | lokal, izin OJK, pasangan IDR | 0,21% dipotong exchange | tidak, dievaluasi bot | tidak ada |
| Tokocrypto | lokal, izin OJK, pasangan USDT | 0,21% dipotong exchange | ada | tidak ada |
| Binance | internasional | 1% kamu setor sendiri | ada | ada |

## Pengaman di mode live

Semua ini berjalan di kode, bukan di prompt AI:

- Bot menolak start tanpa tiga hal sekaligus: `EXECUTION=live`, frasa `LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY`, dan API key.
- Batas nilai per order (`MAX_ORDER_NOTIONAL`), batas jumlah order per hari (`MAX_DAILY_ORDERS`), posisi maksimum 50% ekuitas.
- Saat start, saldo di exchange dicocokkan dengan catatan lokal. Kalau beda, bot menolak jalan.
- Tiap candle, bot mengecek apakah posisi ditutup di luar bot (stop exchange terpicu, jual manual) dan mencatatnya.
- `python -m claudetrader panic` membuat file STOP: loop menutup posisi di harga pasar lalu berhenti.
- Notifikasi Telegram opsional untuk tiap order dan error.
- Data live disimpan terpisah di `data/live/` dan tidak bisa tertukar dengan data paper.
- API key sebaiknya hanya punya izin trade, tanpa withdrawal, dengan IP whitelist. Kode tidak pernah butuh izin withdrawal.

## Hasil strategi contoh (jujur)

BTC/USDT, data Binance, 3000 candle terakhir, diambil 13 Oktober 2026:

| Timeframe | Pajak jual | Strategi | Buy & hold |
|---|---|---|---|
| 1h | 1% | −12,1% | +30,7% |
| 1h | 0,21% | −6,4% | +31,8% |
| 4h | 1% | +2,8% | −25,8% |
| 4h | 0,21% | +7,2% | −25,2% |

Strategi contohnya **belum terbukti untung**. Di periode naik ia kalah dari sekadar hold; di periode turun ia selamat karena menghindar. Ini sengaja tidak di-tuning supaya terlihat apa adanya. Bagian yang bernilai dari proyek ini adalah kerangkanya: cara menguji, cara membatasi risiko, cara mencatat, dan cara pindah ke live dengan aman. Strateginya adalah bagian yang harus kamu kembangkan sendiri lewat `strategy.py` dan diuji ulang.

## Struktur kode

```
claudetrader/
  config.py          semua setting dari environment / .env
  data.py            ambil candle lewat ccxt, cache CSV
  indicators.py      EMA, RSI, ATR
  strategy.py        aturan entry/exit
  risk.py            batas risiko dan ukuran posisi
  reviewer.py        Claude sebagai reviewer (structured output)
  broker.py          paper broker
  live_broker.py     eksekusi sungguhan lewat ccxt
  engine.py          pipeline per candle, dipakai backtest dan loop
  runner.py          loop paper/live, state tahan restart, file STOP
  metrics.py         return, drawdown, Sharpe, win rate
  journal.py         jurnal JSONL
  dashboard/         halaman Lightweight Charts
  cli.py             perintah: fetch, backtest, paper, live, balance, panic, serve
tests/               46 tes, termasuk exchange tiruan untuk alur order
docs/DEPLOY.md       cara jalan di laptop dan VPS
```

## Yang tidak ada (sengaja)

- Short dan leverage. Hanya long spot, satu pair, satu posisi.
- Limit order pasif atau simulasi order book. Hanya market order.
- Prediksi harga oleh AI. Claude tidak pernah diminta menebak arah pasar.
- Jaminan hasil. Lihat tabel di atas.

## Jalur yang disarankan

1. Backtest di beberapa timeframe dan periode, pahami kenapa menang atau kalah.
2. Paper trading minimal 3 bulan di laptop atau VPS, bandingkan dengan buy & hold setelah biaya.
3. Ubah strategi, tambah tes, ulangi langkah 1–2.
4. Testnet Binance beberapa minggu (atau nominal minimum di exchange lokal).
5. Uang asli dengan nominal yang rela hilang seluruhnya.
