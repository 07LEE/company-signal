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
           "enpMainBizNm": "VARCHAR", "sicNm": "VARCHAR", "lastOpegDt": "VARCHAR"}]'
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
    case when tenure_text_years(text) > 0 and tenure_text_years(text) <= 60 then tenure_text_years(text) end
{% endmacro %}
