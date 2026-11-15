-- SQL functions shared by the dbt models, the collector and the lookup.
--
-- This file is the only definition. dbt registers it through the plugin in
-- company_signal/dbt_plugin.py, and the lookup and the tests through
-- company_signal.sqlfunctions.register_functions, so the rules are the same
-- whether a warehouse has been built or not.

-- normalize_name: search and matching key for a workplace name.
--
-- 1. Full-width characters become their ASCII form, so "（주）" equals "(주)".
-- About 7% of names are written with full-width brackets.
-- 2. Letters are upper-cased.
-- 3. Legal-form markers and whitespace are dropped, so "(주)한빛",
-- "주식회사 한빛" and "한빛" compare equal.
--
-- Bracketed text is otherwise kept. A bracketed English name looks like noise,
-- but brackets also tell units apart, as in "(1BL)" and "(2BL)" or "(1)" and
-- "(3)"; dropping them merged 166 different workplaces in a 13-month test.
--
-- The lookup CLI calls the same function on the search text.
--
-- The other functions read company disclosures. The collector, the dbt models
-- and the lookup all go through them, so a disclosure is read the same way
-- whichever way it came in.
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

-- fsc_query_name: the text to search the corporate outline API with.
-- The API matches on the plain company name, and a bracketed addition such as
-- an English name makes it find nothing. A wrong match is ruled out later by
-- the business registration number.
create or replace macro fsc_query_name(name_norm) as
    rtrim(regexp_replace(name_norm, '\(.*', ''), '-');

-- fsc_outline_items: the corporations in one API response, as a list of structs.
-- Only the fields the project uses are read. An empty answer gives an empty list.
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

-- fsc_amount: a disclosed count or amount. The API sends text, and 0 or an
-- empty string where the company disclosed nothing.
create or replace macro fsc_amount(text) as
    nullif(try_cast(nullif(trim(text), '') as bigint), 0);

-- parse_tenure_years: the average tenure as companies write it, in years.
-- Seen so far: "15.0", "9.32", "16년 11월", "약9년9개월", "80개월". Anything
-- else, zero, or more than 60 years gives NULL.
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

-- fsc_can_ask: whether a workplace can be asked about by name.
-- Head offices of for-profit corporations only, by the two middle digits of
-- the business registration number: a branch (85) has its own number and
-- cannot be tied to its company by number, and individual businesses are not
-- in the API. Construction sites and day-labour units are registered under the
-- contractor's name plus a description; they are not companies.
create or replace macro fsc_can_ask(workplace_type_code, biz_reg_no6, workplace_name, name_norm) as
    workplace_type_code = '1'
    and substr(biz_reg_no6, 4, 2) in ('81', '86', '87', '88')
    and not contains(workplace_name, '/')
    and not contains(workplace_name, '일용')
    and not contains(workplace_name, '상용')
    and length(fsc_query_name(name_norm)) >= 2;

-- The three table functions below hold the rules for reading answers, choosing
-- a corporation's figures and tying workplaces to corporations. Each takes the
-- names of the tables to read, so the dbt models run them over everything
-- collected and the lookup runs them over the answer for one workplace.

-- fsc_outline_rows: one row per corporation in each stored answer.
-- page_table has query_name, page and body.
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

-- fsc_corporations: one row per corporation, from the rows of fsc_outline_rows.
-- The figures (employees, tenure, pay) come together from the latest snapshot
-- that has any of them, so they describe the same report. Later snapshots often
-- carry the name only, with the figures left at 0. Descriptive fields take the
-- latest value that is filled in; the latest snapshot can even have an empty
-- registration number.
--
-- Snapshots are ordered by the date they were last published, then by the
-- date they were first published, so a report published later wins over one
-- published earlier on the same day. The last keys only make the choice the
-- same on every run.
--
-- The date shown with the figures is when their snapshot was first published.
-- The last-published date moves forward each time the record is processed
-- again, even when nothing in it changed.
create or replace macro fsc_corporations(item_table) as table
    with item as (
        from query_table(item_table)
    ),
    figures as (
        select crno, employee_count, avg_tenure_years, avg_yearly_pay,
            coalesce(first_published_on, published_on) as figures_published_on
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

-- fsc_ties: workplaces tied to a corporation, as (workplace_id, crno).
-- workplace_table has workplace_id, biz_reg_no6 and name_norm. Among the
-- corporations found by the workplace's name, those whose registration number
-- starts with its published 6 digits are candidates. Only a workplace with
-- exactly one candidate is tied: with two, either could be the wrong company.
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
    having count(*) = 1;

-- The pension data. The dbt models run these over everything collected, and
-- the lookup runs them over the rows it fetched for one workplace.

-- nps_workplace_months: One row per workplace and base month.
--
-- The source has no workplace key, and only the first 6 digits of the business
-- registration number are published. The key is built from those 6 digits, the
-- normalized name and the date the workplace joined the pension scheme.
--
-- The address is deliberately not part of the key: about 2,400 workplaces a
-- month move, and with the legal-dong code in the key each of them looked like
-- one workplace closing and another opening. Source rows that share a key in
-- the same month (about 0.02% of rows) are units the data cannot tell apart;
-- they are summed and counted in source_row_count.
--
-- row_table has the rows of nps_record_rows plus published_on; selected_table
-- has (publication_id, base_ym) of the publication chosen for each month.
create or replace macro nps_workplace_months(row_table, selected_table) as table
    with selected as (

        select r.*
        from query_table(row_table) as r
        inner join query_table(selected_table) using (publication_id, base_ym)

    ),

    deduplicated as (

        -- The source lists a few records twice, 0 to 3 a month, mostly construction
        -- sites with no subscribers. They are not a paging artifact: the two copies
        -- sit at fixed positions pages apart, and refetched pages are byte-identical.
        select distinct * exclude (page, row_no)
        from selected

    ),

    keyed as (

        select
            *,
            normalize_name(workplace_name) as name_norm
        from deduplicated

    )

    select
        md5(concat_ws('|', biz_reg_no6, name_norm, coalesce(applied_on::varchar, ''))) as workplace_id,
        base_ym,
        arg_max(workplace_name, subscriber_count) as workplace_name,
        name_norm,
        biz_reg_no6,
        applied_on,
        arg_max(legal_dong_code, subscriber_count) as legal_dong_code,
        min(join_status_code) as join_status_code,
        arg_max(workplace_type_code, subscriber_count) as workplace_type_code,
        arg_max(road_address, subscriber_count) as road_address,
        arg_max(lot_address, subscriber_count) as lot_address,
        arg_max(industry_code, subscriber_count) as industry_code,
        arg_max(industry_name, subscriber_count) as industry_name,
        max(withdrawn_on) as withdrawn_on,
        sum(subscriber_count)::integer as subscriber_count,
        sum(billed_amount)::bigint as billed_amount,
        sum(new_subscriber_count)::integer as new_subscriber_count,
        sum(lost_subscriber_count)::integer as lost_subscriber_count,
        count(*)::integer as source_row_count
    from keyed
    group by base_ym, biz_reg_no6, name_norm, applied_on;

-- nps_monthly: Monthly indicators per workplace.
--
-- Rates use the previous month's subscriber count, and only when the previous
-- row really is the previous month. Estimated income divides the billed
-- contribution by headcount and the contribution rate; contributions are capped
-- by the income ceiling, so high-income workplaces are underestimated.
--
-- month_table has the rows of nps_workplace_months.
create or replace macro nps_monthly(month_table) as table
    with monthly as (

        select
            workplace_id,
            base_ym,
            subscriber_count,
            billed_amount,
            new_subscriber_count,
            lost_subscriber_count,
            source_row_count,
            lag(base_ym) over by_workplace as previous_row_ym,
            lag(subscriber_count) over by_workplace as previous_row_subscriber_count
        from query_table(month_table)
        window by_workplace as (partition by workplace_id order by base_ym)

    ),

    with_previous as (

        select
            * exclude (previous_row_ym, previous_row_subscriber_count),
            case
                when previous_row_ym = base_ym - interval 1 month then previous_row_subscriber_count
            end as previous_subscriber_count
        from monthly

    )

    select
        m.workplace_id,
        m.base_ym,
        m.subscriber_count,
        m.billed_amount,
        m.new_subscriber_count,
        m.lost_subscriber_count,
        m.previous_subscriber_count,
        m.new_subscriber_count / nullif(m.previous_subscriber_count, 0) as hire_rate,
        m.lost_subscriber_count / nullif(m.previous_subscriber_count, 0) as attrition_rate,
        nps_contribution_rate(m.base_ym) as contribution_rate,
        round(m.billed_amount / nullif(m.subscriber_count, 0) / nps_contribution_rate(m.base_ym))::bigint as est_avg_monthly_income,
        m.source_row_count
    from with_previous as m;

-- nps_workplaces: One row per workplace: its latest description, for search and selection,
-- plus the 12-month attrition, which is only given with 12 full months of history.
--
-- month_table has the rows of nps_workplace_months, monthly_table those of
-- nps_monthly. The latest data month is the latest month in month_table.
create or replace macro nps_workplaces(month_table, monthly_table) as table
    with latest as (

        select *
        from query_table(month_table)
        qualify row_number() over (partition by workplace_id order by base_ym desc) = 1

    ),

    history as (

        select
            workplace_id,
            min(base_ym) as first_base_ym,
            count(*)::integer as months_observed
        from query_table(monthly_table)
        group by workplace_id

    ),

    last_12_months as (

        select
            m.workplace_id,
            count(*) as month_count,
            sum(m.lost_subscriber_count) as lost_subscriber_count,
            avg(m.subscriber_count) as avg_subscriber_count
        from query_table(monthly_table) as m
        inner join latest as l using (workplace_id)
        where m.base_ym > l.base_ym - interval 12 month
        group by m.workplace_id

    ),

    data_bounds as (

        select max(base_ym) as latest_data_ym from query_table(month_table)

    )

    select
        l.workplace_id,
        l.workplace_name,
        l.name_norm,
        l.biz_reg_no6,
        l.road_address,
        l.lot_address,
        l.legal_dong_code,
        l.industry_code,
        l.industry_name,
        l.workplace_type_code,
        l.join_status_code,
        l.applied_on,
        l.withdrawn_on,
        l.subscriber_count,
        l.base_ym as latest_base_ym,
        l.base_ym = b.latest_data_ym as in_latest_data,
        h.first_base_ym,
        h.months_observed,
        case
            when t.month_count = 12 and t.avg_subscriber_count > 0
                then t.lost_subscriber_count / t.avg_subscriber_count
        end as attrition_rate_12m
    from latest as l
    inner join history as h using (workplace_id)
    left join last_12_months as t using (workplace_id)
    cross join data_bounds as b;

-- nps_years: One row per workplace and calendar year.
--
-- A year with fewer than 12 months of data is kept and flagged, not scaled up:
-- nine months of leavers are not a yearly attrition. Rates divide by the
-- average monthly headcount of the months that are present.
--
-- replacement_rate is the smaller of joiners and leavers over the headcount:
-- the share of staff that was swapped for someone else. A growing workplace has
-- many joiners and a low replacement rate; one that keeps losing and rehiring
-- people has a high one at the same headcount.
--
-- monthly_table has the rows of nps_monthly.
create or replace macro nps_years(monthly_table) as table
    with yearly as (

        select
            workplace_id,
            year(base_ym)::integer as base_year,
            count(*)::integer as months_observed,
            avg(subscriber_count) as avg_subscriber_count,
            arg_min(subscriber_count, base_ym) as first_subscriber_count,
            arg_max(subscriber_count, base_ym) as last_subscriber_count,
            sum(new_subscriber_count)::integer as new_subscriber_total,
            sum(lost_subscriber_count)::integer as lost_subscriber_total,
            -- Total estimated income over total subscriber-months, so a month with
            -- more people weighs more. fsum adds with error compensation: a plain sum of
            -- doubles depends on the order rows arrive in, which moved some values by 1 won.
            round(fsum(billed_amount / contribution_rate) / nullif(sum(subscriber_count), 0))::bigint
                as est_avg_monthly_income
        from query_table(monthly_table)
        group by workplace_id, year(base_ym)

    )

    select
        workplace_id,
        base_year,
        months_observed,
        months_observed = 12 as is_full_year,
        avg_subscriber_count,
        first_subscriber_count,
        last_subscriber_count,
        new_subscriber_total,
        lost_subscriber_total,
        lost_subscriber_total / nullif(avg_subscriber_count, 0) as attrition_rate,
        least(new_subscriber_total, lost_subscriber_total) / nullif(avg_subscriber_count, 0) as replacement_rate,
        est_avg_monthly_income,
        -- Change from the year before, only when the row before really is the year before.
        case
            when lag(base_year) over by_workplace = base_year - 1
                then est_avg_monthly_income / nullif(lag(est_avg_monthly_income) over by_workplace, 0) - 1
        end as income_change_rate
    from yearly
    window by_workplace as (partition by workplace_id order by base_year);

-- nps_overall: One row per workplace: the whole collected period in a few numbers.
--
-- Attrition and replacement are given for three spans, each as a yearly rate:
-- the whole period, the last 3 years and the last 12 months, counted back from
-- the workplace's own latest month. A span is left empty unless every month of
-- it is present: 12 months for the last year, 36 for the last three, and at
-- least 12 for the whole period. Side by side they show whether the workplace
-- has always been like this or changed recently.
--
-- implied_tenure_years is 1 / yearly attrition: how long people would stay on
-- average if they kept leaving at that pace. It is not a measured tenure.
--
-- monthly_table has the rows of nps_monthly, workplace_table those of
-- nps_workplaces.
create or replace macro nps_overall(monthly_table, workplace_table) as table
    with monthly as (

        select * from query_table(monthly_table)

    ),

    totals as (

        select
            workplace_id,
            min(base_ym) as first_base_ym,
            max(base_ym) as last_base_ym,
            count(*)::integer as months_observed,
            arg_min(subscriber_count, base_ym) as first_subscriber_count,
            arg_max(subscriber_count, base_ym) as last_subscriber_count,
            avg(subscriber_count) as avg_subscriber_count,
            sum(new_subscriber_count)::integer as new_subscriber_total,
            sum(lost_subscriber_count)::integer as lost_subscriber_total,
            arg_min(est_avg_monthly_income, base_ym) filter (where est_avg_monthly_income is not null)
                as first_est_income,
            min(base_ym) filter (where est_avg_monthly_income is not null) as first_est_income_ym,
            arg_max(est_avg_monthly_income, base_ym) filter (where est_avg_monthly_income is not null)
                as last_est_income,
            max(base_ym) filter (where est_avg_monthly_income is not null) as last_est_income_ym
        from monthly
        group by workplace_id

    ),

    -- Sums over the last 12 and the last 36 months of each workplace.
    recent as (

        select
            m.workplace_id,
            count(*) filter (where m.base_ym > t.last_base_ym - interval 12 month) as months_12m,
            sum(m.new_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as new_12m,
            sum(m.lost_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as lost_12m,
            avg(m.subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as avg_12m,
            count(*) filter (where m.base_ym > t.last_base_ym - interval 36 month) as months_3y,
            sum(m.new_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as new_3y,
            sum(m.lost_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as lost_3y,
            avg(m.subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as avg_3y
        from monthly as m
        inner join totals as t using (workplace_id)
        group by m.workplace_id

    ),

    rates as (

        select
            t.*,
            case
                when t.months_observed >= 12
                    then t.lost_subscriber_total * 12.0 / t.months_observed / nullif(t.avg_subscriber_count, 0)
            end as attrition_rate_per_year,
            case
                when t.months_observed >= 12
                    then least(t.new_subscriber_total, t.lost_subscriber_total) * 12.0
                        / t.months_observed / nullif(t.avg_subscriber_count, 0)
            end as replacement_rate_per_year,
            case when c.months_3y = 36 then c.lost_3y / 3.0 / nullif(c.avg_3y, 0) end as attrition_rate_3y,
            case when c.months_3y = 36 then least(c.new_3y, c.lost_3y) / 3.0 / nullif(c.avg_3y, 0) end
                as replacement_rate_3y,
            case when c.months_12m = 12 then c.lost_12m / nullif(c.avg_12m, 0) end as attrition_rate_12m,
            case when c.months_12m = 12 then least(c.new_12m, c.lost_12m) / nullif(c.avg_12m, 0) end
                as replacement_rate_12m,
            datediff('month', t.first_est_income_ym, t.last_est_income_ym) as income_span_months
        from totals as t
        inner join recent as c using (workplace_id)

    )

    select
        r.workplace_id,
        r.first_base_ym,
        r.last_base_ym,
        r.months_observed,
        r.first_subscriber_count,
        r.last_subscriber_count,
        r.avg_subscriber_count,
        r.new_subscriber_total,
        r.lost_subscriber_total,
        r.attrition_rate_per_year,
        r.attrition_rate_3y,
        r.attrition_rate_12m,
        r.replacement_rate_per_year,
        r.replacement_rate_3y,
        r.replacement_rate_12m,
        1 / nullif(r.attrition_rate_per_year, 0) as implied_tenure_years,
        1 / nullif(r.attrition_rate_3y, 0) as implied_tenure_years_3y,
        1 / nullif(r.attrition_rate_12m, 0) as implied_tenure_years_12m,
        r.first_est_income,
        r.last_est_income,
        -- Compound yearly growth between the first and the last month with an estimate.
        case
            when r.income_span_months >= 12 and r.first_est_income > 0
                then power(r.last_est_income / r.first_est_income, 12.0 / r.income_span_months) - 1
        end as income_growth_per_year,
        -- Nobody can have been there longer than the workplace has been in the scheme.
        datediff('month', w.applied_on, r.last_base_ym) / 12.0 as scheme_age_years
    from rates as r
    inner join query_table(workplace_table) as w using (workplace_id);

-- nps_selected_publications: one publication per base month. The base month
-- comes from the records, not from the publication title. When a month was
-- published more than once, the latest publication wins; the others stay in
-- raw only.
--
-- row_table has the rows of nps_record_rows plus published_on.
create or replace macro nps_selected_publications(row_table) as table
    with per_publication as (
        select publication_id, published_on, base_ym, count(*) as row_count
        from query_table(row_table)
        group by all
    ),
    ranked as (
        select
            *,
            row_number() over (partition by base_ym order by published_on desc, publication_id) as pick_order,
            count(*) over (partition by base_ym) as publication_count
        from per_publication
    )
    select publication_id, base_ym, published_on, row_count, publication_count
    from ranked
    where pick_order = 1;
