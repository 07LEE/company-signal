# company-signal

Look up a Korean workplace by name and see how its headcount, joiners, leavers and estimated pay have moved month by month.

The numbers come from the National Pension Service workplace data on the Korean public data portal. The tool shows indicators and their limits. It does not rate or rank workplaces.

## Quick start

You need Python 3.11 or later and a free API key from [data.go.kr](https://www.data.go.kr) for the pension dataset [15083277](https://www.data.go.kr/data/15083277/fileData.do). Apply for the corporate outline dataset [15043184](https://www.data.go.kr/data/15043184/openapi.do) with the same account to see company details.

```bash
# 1. Install
pip install -e .

# 2. Put the URL-encoded key from the portal in a .env file
echo "ODCLOUD_SERVICE_KEY_ENCODED=your-url-encoded-key" > .env

# 3. Search by name, then type a number to open a workplace
company-signal search 한빛
```

With nothing collected, the search asks the pension API directly: the latest month by name, then the earlier months of the workplace you pick. Answers are stored, so asking again costs no calls.

To search the whole data locally instead, collect it and build the lookup tables from the repository root:

```bash
company-signal update       # the 13 newest monthly publications, about 3 minutes each
```

This runs `collect`, the dbt build, `enrich` (company details, up to the daily API limit) and a build of the company details, in that order. A build is skipped when there is nothing new. Every step can be interrupted and resumes when the command is run again, so running it daily keeps the data current. `--months 132` collects the whole history since 2015.

An opened workplace can be paged by month and switched to a yearly report, a summary of the whole period, or the company's own disclosure; the keys are shown on screen. Each command lists its options with `--help`.

## Example

The workplace and its values below are made up.

```text
(주)한빛
  address    서울특별시 구로구 디지털로
  industry   응용 소프트웨어 개발 및 공급업
  type       corporation, in the pension scheme since 2015-04-01
  data       2024-02 to 2026-09 (32 months)

Latest month: 2026-09
  subscribers                        120
  joined                               3   2.5% of the previous month's 118
  left                                 1   0.8% of the previous month's 118
  est. avg monthly income   3,850,000 KRW
  12-month attrition               15.2%

Monthly history (2026-08 to 2026-09, 2 of 32 months)
  month    subscribers  joined   left   join %  leave %   est. income
  2026-08          118       4      2     3.4%     1.7%     3,820,000
  2026-09          120       3      1     2.5%     0.8%     3,850,000
[<] older  [>] newer  [a] all  [y] yearly  [s] summary  [d] company  [q] quit:
```

## Notes

- The data is monthly and covers corporations with 3 or more subscribers and individual businesses with 10 or more.
- "joined" and "left" include resumed and suspended pension payments, so they are close to, but not exactly, hires and resignations.
- Estimated income is capped by the pension income ceiling. A high-income workplace comes out lower than it really is.
- The implied tenure in the summary is 1 / attrition, an estimate and not a measured tenure.
- Rates are hidden below 5 staff, and a rate over a span needs every month of that span.
- The source has no workplace identifier, so a workplace that changes its name appears as a new one.
- Company details are what the corporation reports about itself, for the whole company. They are shown only for a head office whose name and business number match one corporation.
