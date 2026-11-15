# company-signal

Look up a Korean workplace by name and see how its headcount, joiners, leavers and estimated pay have moved month by month.

The numbers come from the National Pension Service workplace data on the Korean public data portal. The tool shows indicators and their limits. It does not rate or rank workplaces.

## Quick start

You need Python 3.11 or later and a free [data.go.kr](https://www.data.go.kr) API key for datasets [15083277](https://www.data.go.kr/data/15083277/fileData.do) (pension) and [15043184](https://www.data.go.kr/data/15043184/openapi.do) (company details).

```bash
# 1. Install
pip install -e .

# 2. Put the URL-encoded key from the portal in a .env file
echo "ODCLOUD_SERVICE_KEY_ENCODED=your-url-encoded-key" > .env

# 3. Search by name, then type a number to open a workplace
company-signal search 한빛
```

With nothing collected, the search asks the API directly and stores the answers.

To collect the data and build the lookup tables locally, run this from the repository root. It resumes where it stopped and skips builds when nothing is new.

```bash
company-signal update       # --months 132 for the whole history since 2015
```

Each command lists its options with `--help`.

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
- "joined" and "left" include resumed and suspended payments, so they are not exactly hires and resignations.
- Estimated income is capped by the pension income ceiling, so high incomes come out low.
- Implied tenure is 1 / attrition, not a measured tenure.
- Rates are hidden below 5 staff.
- A workplace that changes its name appears as a new one, unless only an English name or a legal-form word was added or dropped.
- Company details are reported by the company itself, for the whole company.
