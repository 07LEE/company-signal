{# normalize_name: search and matching key for a workplace name.

   1. Full-width characters become their ASCII form, so "（주）" equals "(주)".
      About 7% of names are written with full-width brackets.
   2. Letters are upper-cased.
   3. Legal-form markers and whitespace are dropped, so "(주)한빛",
      "주식회사 한빛" and "한빛" compare equal.

   Bracketed text is otherwise kept. A bracketed English name looks like noise,
   but brackets also tell units apart, as in "(1BL)" and "(2BL)" or "(1)" and
   "(3)"; dropping them merged 166 different workplaces in a 13-month test.

   The lookup CLI calls the same function on the search text.

   The other functions read company disclosures. The collector, the dbt models
   and the lookup all go through them, so a disclosure is read the same way
   whichever way it came in. #}
{% macro create_functions() %}
create or replace macro normalize_name(name) as
    regexp_replace(
        upper(replace(
            translate(
                name,
                '！＂＃＄％＆＇（）＊＋，－．／０１２３４５６７８９：；＜＝＞？＠ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ［＼］＾＿｀ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ｛｜｝～　',
                '!"#$%&''()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~ '
            ),
            '㈜', '(주)'
        )),
        '\(주\)|주식회사|\(유\)|\(유한\)|유한책임회사|유한회사|\(합\)|\(합자\)|합자회사|\(자\)|\(명\)|합명회사|\(사\)|사단법인|\(재\)|재단법인|\s',
        '',
        'g'
    )
;

{# fsc_query_name: the text to search the corporate outline API with.
   The API matches on the plain company name, and a bracketed addition such as
   an English name makes it find nothing. A wrong match is ruled out later by
   the business registration number. #}
create or replace macro fsc_query_name(name_norm) as
    rtrim(regexp_replace(name_norm, '\(.*', ''), '-');

{# fsc_outline_items: the corporations in one API response, as a list of structs.
   Only the fields the project uses are read. An empty answer gives an empty list. #}
create or replace macro fsc_outline_items(body) as
    from_json(
        case json_type(json_extract(body, '$.response.body.items.item'))
            -- A single match could come as an object instead of a list.
            when 'OBJECT' then ('[' || json_extract(body, '$.response.body.items.item')::varchar || ']')::json
            else json_extract(body, '$.response.body.items.item')
        end,
        '[{"crno": "VARCHAR", "bzno": "VARCHAR", "corpNm": "VARCHAR", "enpEstbDt": "VARCHAR",
           "enpEmpeCnt": "VARCHAR", "empeAvgCnwkTermCtt": "VARCHAR", "enpPn1AvgSlryAmt": "VARCHAR",
           "enpMainBizNm": "VARCHAR", "sicNm": "VARCHAR", "fstOpegDt": "VARCHAR", "lastOpegDt": "VARCHAR"}]'
    );

{# fsc_amount: a disclosed count or amount. The API sends text, and 0 or an
   empty string where the company disclosed nothing. #}
create or replace macro fsc_amount(text) as
    nullif(try_cast(nullif(trim(text), '') as bigint), 0);

{# parse_tenure_years: the average tenure as companies write it, in years.
   Seen so far: "15.0", "9.32", "16년 11월", "약9년9개월", "80개월". Anything
   else, zero, or more than 60 years gives NULL. #}
create or replace macro tenure_text_years(text) as
    case
        when regexp_full_match(regexp_replace(coalesce(text, ''), '약|\s', '', 'g'), '\d+(\.\d+)?')
            then try_cast(regexp_replace(text, '약|\s', '', 'g') as double)
        when regexp_full_match(regexp_replace(coalesce(text, ''), '약|\s', '', 'g'), '(\d+(\.\d+)?년)?(\d+(\.\d+)?개?월)?')
            then coalesce(try_cast(regexp_extract(text, '(\d+(?:\.\d+)?)\s*년', 1) as double), 0)
                + coalesce(try_cast(regexp_extract(text, '(\d+(?:\.\d+)?)\s*개?\s*월', 1) as double), 0) / 12
    end;

create or replace macro parse_tenure_years(text) as
    case when tenure_text_years(text) > 0 and tenure_text_years(text) <= 60 then tenure_text_years(text) end;

{# fsc_can_ask: whether a workplace can be asked about by name.
   Head offices of for-profit corporations only, by the two middle digits of
   the business registration number: a branch (85) has its own number and
   cannot be tied to its company by number, and individual businesses are not
   in the API. Construction sites and day-labour units are registered under the
   contractor's name plus a description; they are not companies. #}
create or replace macro fsc_can_ask(workplace_type_code, biz_reg_no6, workplace_name, name_norm) as
    workplace_type_code = '1'
    and substr(biz_reg_no6, 4, 2) in ('81', '86', '87', '88')
    and not contains(workplace_name, '/')
    and not contains(workplace_name, '일용')
    and not contains(workplace_name, '상용')
    and length(fsc_query_name(name_norm)) >= 2;

{# The three table functions below hold the rules for reading answers, choosing
   a corporation's figures and tying workplaces to corporations. Each takes the
   names of the tables to read, so the dbt models run them over everything
   collected and the lookup runs them over the answer for one workplace. #}

{# fsc_outline_rows: one row per corporation in each stored answer.
   page_table has query_name, page and body. #}
create or replace macro fsc_outline_rows(page_table) as table
    select
        p.query_name,
        p.page,
        item.crno,
        item.bzno,
        item.corpNm as corp_name,
        try_strptime(nullif(item.enpEstbDt, ''), '%Y%m%d')::date as established_on,
        fsc_amount(item.enpEmpeCnt)::integer as employee_count,
        parse_tenure_years(item.empeAvgCnwkTermCtt) as avg_tenure_years,
        fsc_amount(item.enpPn1AvgSlryAmt) as avg_yearly_pay,
        nullif(trim(item.enpMainBizNm), '') as main_business,
        nullif(trim(item.sicNm), '') as industry_name,
        try_strptime(nullif(item.fstOpegDt, ''), '%Y%m%d')::date as first_published_on,
        try_strptime(nullif(item.lastOpegDt, ''), '%Y%m%d')::date as published_on
    from query_table(page_table) as p,
        unnest(fsc_outline_items(p.body)) as t (item)
    where item.crno is not null and item.crno <> '';

{# fsc_corporations: one row per corporation, from the rows of fsc_outline_rows.
   The figures (employees, tenure, pay) come together from the latest snapshot
   that has any of them, so they describe the same report. Later snapshots often
   carry the name only, with the figures left at 0. Descriptive fields take the
   latest value that is filled in; the latest snapshot can even have an empty
   registration number.

   Snapshots are ordered by the date they were last published, then by the
   date they were first published, so a report published later wins over one
   published earlier on the same day. The last keys only make the choice the
   same on every run. #}
create or replace macro fsc_corporations(item_table) as table
    with item as (
        from query_table(item_table)
    ),
    figures as (
        select crno, employee_count, avg_tenure_years, avg_yearly_pay, published_on as figures_published_on
        from item
        where employee_count is not null or avg_tenure_years is not null or avg_yearly_pay is not null
        qualify row_number() over (
            partition by crno
            order by published_on desc nulls last, first_published_on desc nulls last,
                employee_count desc nulls last, avg_yearly_pay desc nulls last, avg_tenure_years desc nulls last
        ) = 1
    )
    select
        i.crno,
        first(i.corp_name order by i.published_on desc nulls last, i.first_published_on desc nulls last, i.corp_name)
            filter (where nullif(i.corp_name, '') is not null) as corp_name,
        first(i.bzno order by i.published_on desc nulls last, i.first_published_on desc nulls last, i.bzno)
            filter (where nullif(i.bzno, '') is not null) as bzno,
        first(i.established_on order by i.published_on desc nulls last, i.first_published_on desc nulls last, i.established_on)
            filter (where i.established_on is not null) as established_on,
        first(i.main_business order by i.published_on desc nulls last, i.first_published_on desc nulls last, i.main_business)
            filter (where i.main_business is not null) as main_business,
        first(i.industry_name order by i.published_on desc nulls last, i.first_published_on desc nulls last, i.industry_name)
            filter (where i.industry_name is not null) as industry_name,
        max(i.published_on) as latest_published_on,
        any_value(f.employee_count) as employee_count,
        any_value(f.avg_tenure_years) as avg_tenure_years,
        any_value(f.avg_yearly_pay) as avg_yearly_pay,
        any_value(f.figures_published_on) as figures_published_on
    from item as i
    left join figures as f using (crno)
    group by i.crno;

{# fsc_ties: workplaces tied to a corporation, as (workplace_id, crno).
   workplace_table has workplace_id, biz_reg_no6 and name_norm. Among the
   corporations found by the workplace's name, those whose registration number
   starts with its published 6 digits are candidates. Only a workplace with
   exactly one candidate is tied: with two, either could be the wrong company. #}
create or replace macro fsc_ties(workplace_table, item_table) as table
    with workplace as (
        select workplace_id, biz_reg_no6, fsc_query_name(name_norm) as query_name
        from query_table(workplace_table)
    ),
    candidate as (
        select distinct w.workplace_id, i.crno
        from workplace as w
        inner join query_table(item_table) as i
            on i.query_name = w.query_name
            and left(i.bzno, 6) = w.biz_reg_no6
    )
    select workplace_id, any_value(crno) as crno
    from candidate
    group by workplace_id
    having count(*) = 1
{% endmacro %}
