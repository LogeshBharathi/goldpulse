# GoldPulse

GoldPulse estimates the next Tamil Nadu 22 karat gold board rate (1 gram, before GST) and publishes it as a static page. GitHub Actions runs the jobs. GitHub Pages serves `docs/`. There is no server and no paid service. All times are Asia/Kolkata.

The estimate starts from today's board and moves it by part of the change in international gold, priced in rupees, since 09:00 IST. A second model applies the usual local premium to the latest international price. The model with the lower error over the last 20 scored days is the champion. Until that history exists, the beta model is used. The range is a split-conformal interval from past misses, widened when recent overnight moves have been large, and never narrower than the minimum in `config.yaml`.

## Setup

```bash
pip install -r requirements.txt
pytest
```

Optional, for failure messages: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. If either is missing, `src/notify.py` does nothing.

## Schedule

| Workflow | UTC cron | IST | What it does |
|---|---|---|---|
| `evening.yml` | 30 16 * * * | 22:00 | `forecast.py --kind evening`, rebuild the page, commit |
| `final.yml` | 15 0 * * * | 05:45 | `forecast.py --kind final`, rebuild the page, commit |
| `actual.yml` | 0 5, 30 6, 30 8 * * * | 10:30, 12:00, 14:00 | save today's board if it is missing, score, rebuild, commit |

Each workflow can also be started by hand. They share one concurrency group, install Python 3.12, and commit as `github-actions[bot]` only when files changed. A second run of the same job overwrites the same forecast row instead of adding another. `git pull --rebase` runs before the push. A failure calls `src/notify.py`.

The evening run targets the next calendar day. The final run, made before 09:00, targets today's board. On Saturday and Sunday the international move is treated as zero, because COMEX is shut. A Sunday board repeats Saturday when `weekend.sunday_repeats_saturday` is true.

## Add a board rate by hand

Put the date and the 1 gram 22K rate, before GST, in `data/manual_board.json`:

```json
{"2026-11-20": 11510}
```

The manual value is used only when BankBazaar and GoodReturns are missing or fail the checks. A rate more than 5% away from the previous saved board is rejected and is not learned from. If the two websites differ by more than 0.5%, the chosen rate is kept and the difference is logged.

## Backfill

```bash
python -m src.backfill path/to/board.csv
```

The CSV needs columns `date` and `rate_22k_1g`. The command also downloads about two years of hourly COMEX and USD/INR prices and caches them in `data/intl_hourly.csv`.

## How it corrects itself

After the real board is saved, `score_and_learn.py` fills `actual` and `error` on that day's evening and final forecasts. It refits the ridge beta (30-day window, prior 1.0) and the exponentially weighted premium, and writes `data/params.json`. If the error is more than 4 times the rolling error standard deviation, the miss is stored but the parameters are left as they were.

The page then shows that estimate next to the actual, plus the last 30 days of average miss, how often the actual landed inside the range, and how often the direction was right.

## Units

The 1 gram rate is rounded to a whole rupee. A pavun is 8 grams, so 11510 becomes 92080. Ten grams and 100 grams are the same rounded rate times 10 and 100.

## Limitations

- Overnight moves happen after the evening estimate. They are the main source of error, and no version of this method can see them in advance.
- The learned share of the international move needs scored days. With a short history it stays close to the prior.
- BankBazaar and GoodReturns are public pages, not an official feed, and they can be late or disagree.
- The published range is sized from past misses. On a day that moves more than those days did, the actual can fall outside it.
- GST and making charges are not included. Shop rates differ from the board.
- This is an estimate, not a promise, and it is not financial advice.

## Also in this repo

`goldpulse/` is a local FastAPI service that forecasts the MCX session from COMEX, XAU/USD and USD/INR. It needs Postgres and is separate from the GitHub Actions page. See `docs/DATA_SOURCES.md` for the data-licensing notes. The static page does not use that service.
