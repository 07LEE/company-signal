# company-signal

Look up a Korean workplace by name and see how its headcount, joiners, leavers and estimated pay have moved month by month.

The numbers come from the National Pension Service workplace data on the Korean public data portal. The tool shows indicators and their limits. It does not rate or rank workplaces.

## Quick start

You need Python 3.11 or later and a free API key for dataset 15083277 on [data.go.kr](https://www.data.go.kr/data/15083277/fileData.do).

```bash
# 1. Install
pip install -e .

# 2. Put the URL-encoded key from the portal in a .env file
echo "ODCLOUD_SERVICE_KEY_ENCODED=your-url-encoded-key" > .env

# 3. Download the 13 newest monthly publications (about 11 minutes each)
company-signal collect

# 4. Build the lookup tables
dbt build --project-dir transform --profiles-dir transform

# 5. Search by name, then type a number to open a workplace
company-signal search 한빛
```

The download can be interrupted. Run `company-signal collect` again and it resumes where it stopped. After a new publication has been downloaded, run step 4 again to include it.

Each command lists its options with `--help`.

## Example

The workplace and its values below are made up.

```text
(주)한빛
  address    서울특별시 구로구 디지털로
  industry   응용 소프트웨어 개발 및 공급업
  type       corporation, in the pension scheme since 2015-04-01
  data       2026-06 to 2026-09 (4 months)

Latest month: 2026-09
  subscribers                        120
  joined                               3   2.5% of the previous month's 118
  left                                 1   0.8% of the previous month's 118
  est. avg monthly income   3,850,000 KRW
  12-month attrition                   -   needs 12 months of data

Monthly history
  month    subscribers  joined   left   join %  leave %   est. income
  2026-08          118       4      2     3.4%     1.7%     3,820,000
  2026-09          120       3      1     2.5%     0.8%     3,850,000
```

## Notes

- The data is monthly and covers corporations with 3 or more subscribers and individual businesses with 10 or more.
- "joined" and "left" include resumed and suspended pension payments, so they are close to, but not exactly, hires and resignations.
- Estimated income is capped by the pension income ceiling. A high-income workplace comes out lower than it really is.
- Rates are hidden when the previous month had fewer than 5 subscribers, and the 12-month attrition needs 12 full months of data.
- The source has no workplace identifier, so a workplace that changes its name appears as a new one.
