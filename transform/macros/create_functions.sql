{# normalize_name: search and matching key for a workplace name.

   1. Full-width characters become their ASCII form, so "（주）" equals "(주)".
      About 7% of names are written with full-width brackets.
   2. Letters are upper-cased.
   3. Legal-form markers and whitespace are dropped, so "(주)한빛",
      "주식회사 한빛" and "한빛" compare equal.

   Bracketed text is otherwise kept. A bracketed English name looks like noise,
   but brackets also tell units apart, as in "(1BL)" and "(2BL)" or "(1)" and
   "(3)"; dropping them merged 166 different workplaces in a 13-month test.

   The lookup CLI calls the same function on the search text. #}
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
{% endmacro %}
