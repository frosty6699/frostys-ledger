# Frosty's Ledger

![Frosty's Ledger](assets/og.png)

**Read it: [frosty6699.github.io/frostys-ledger](https://frosty6699.github.io/frostys-ledger/)**

A free newspaper that prints itself. At about 6 AM India time, a small Python program reads the public
RSS feeds of 20 publishers and builds the day's paper. The publishers include The Economic Times,
Business Standard, Mint, BusinessLine, CNBC, BBC, The Guardian, Reuters, WSJ, FT and Bloomberg. On
weekdays an evening edition follows at about 4:45 PM, after the Indian market closes.

What's in it:

- **One story, one entry.** When several papers cover the same news, it becomes one story with every
  paper's name on it.
- **A ranked front page.** It holds the stories the most papers are writing about.
- **Sections:** Markets, Economy & Policy, Companies & Deals, World, Technology and Opinion.
- **Markets strip:** Nifty, Sensex, Bank Nifty, rupee, Brent, gold, S&P 500 and the US 10-year yield.
  Tap any of them for a one-year chart.
- **Your watchlist:** chosen stocks with price, daily move and any headlines that mention them.
- **Nifty movers:** the top 5 gainers and losers from the latest session.
- **The week ahead:** RBI and Fed decisions, Indian and US inflation, GDP, jobs reports and market
  holidays.
- **The CFA Lens:** three of the day's stories next to the CFA Level I concept each one illustrates,
  with the exam angle.
- **Regulators:** the latest RBI and US Fed press releases.
- **Subscriber Desk:** nothing gets around a paywall. Subscriber-only stories (WSJ, FT, Bloomberg,
  ET Prime, Mint Premium and others) appear here with only the headline and summary the publisher
  gives away free.
- **Offline mode:** once you've opened the paper, it still loads with no signal.

Every past edition stays online under **All editions**.

## How it works

- `build_paper.py` builds the whole paper, using only Python's standard library. `cfa_lens.py` holds
  the CFA concept library, and `calendar.json` the fixed dates for the week ahead.
- `.github/workflows/print.yml` runs it on GitHub Actions: every morning, plus weekday evenings. The
  workflow saves the edition to `editions/` and publishes the site to GitHub Pages.
- To print a fresh edition now, open **Actions → Print the paper → Run workflow**. This works from the
  GitHub app on a phone too.

## Changing things

| To change | Edit |
|---|---|
| Sources | the `SOURCES` list in `build_paper.py` |
| The watchlist | the `WATCHLIST` list in `build_paper.py`: name, Yahoo Finance symbol (`.NS` for NSE), `inr` or `usd2` |
| Week-ahead dates | `calendar.json`, refreshed each November from the official RBI, Fed, BLS and NSE schedules |

The Actions log warns when `calendar.json` is about to run out of dates. India's inflation, WPI and GDP
release days are worked out automatically.

## Credits

Headlines, summaries and pictures belong to their publishers. Every story links back to the original.
If you're a publisher and would like your feed left out, open an issue and it will be removed.
