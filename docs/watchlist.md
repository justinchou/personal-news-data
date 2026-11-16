# 代码表与交易日历维护（symbols / market calendar）

## symbols.yml 是生成文件

`config/symbols.yml` 由站点源仓库的关注标的配置（watchlist）通过 `scripts/sync_live_symbols.py --write` 生成，
**不要手改**；在源仓库增删改关注标的后重新生成，然后在这里 `git pull --rebase` → commit → push
（定时任务每几分钟会提交数据，先拉再推）。

字段：
- `groups.<组>.source`：`tencent` 或 `coinbase`；`market`（可选）：使用哪个交易日历，缺省 = 组名。
- `symbols[].key`：站点页面 id（股票 = 代码小写、`.` → `-`，如 `0700-hk`；资产 = 资产 id，如 `gold_usd`），即 `prices.json` 里 `quotes` 的键。
- `symbols[].code`：数据源代码；`sym`：展示代码 / 名称。

| 组 | 日历 | code 格式 | 例子 | 开市时频率 | 收盘后 |
|---|---|---|---|---|---|
| us_equity | us_equity | `us` + 代码，无交易所后缀 | `usNVDA`、`usTLT` | 10 分钟 | +20 分钟一次 |
| hk_equity | hk_equity | `hk` + 5 位补零 | `hk00700` | 10 分钟 | +20 分钟一次 |
| cn_equity | cn_equity | `sh` / `sz` + 6 位 | `sh600900`、`sz300308` | 10 分钟 | +15 分钟一次 |
| futures_fx | cme_globex | 腾讯外盘期货 `hf_XX`、汇率 `whXXXYYY` | `hf_CL`、`hf_OIL`、`hf_GC`、`whUSDCNY` | 15 分钟 | +30 分钟一次 |
| crypto | crypto | Coinbase 交易对 | `BTC-USD`、`ETH-USD` | 10 分钟（24/7，写入历史） | — |

腾讯报价接口只认无后缀代码：`usNVDA.OQ` 会让整批返回 `v_pv_none_match`。

## 交易日历（config/market_calendar.yml）

- 每个市场：`tz`、`sessions`（交易所当地时间）、`holidays`、`half_days` / `half_day_sessions`、`early_close`、`valid_through`、`source`（官方页面）。
- 超过 `valid_through` 时按“工作日即开市”处理，`prices.json` 的 `groups.<组>.calendar_valid` 变为 `false`。
- **每年要做**：交易所公布下一年安排后补 `holidays` / `half_days` / `early_close` 并改 `valid_through`，同时在注释里写来源链接：
  - 美股：https://www.nyse.com/markets/hours-calendars（目前到 2027-12-31）
  - 港股：HKEX 每年发布的交易日历通告（目前到 2026-12-31）
  - A 股：上交所“休市安排”公告（目前到 2026-12-31）
  - CME Globex：未载入假日（按开市处理，最多多抓一次）
- 改完运行 `python -m unittest discover -s tests -t .` 和 `python scripts/fetch_live.py --status`。

## 手动运行

Actions → live-prices → Run workflow；`force` 留空 = 按日历正常判断，填 `all` 或组名（逗号分隔）= 强制抓取。
