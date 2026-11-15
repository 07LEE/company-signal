-- Fails when a publication uses a column name that is not in the nps_columns
-- mapping. The first row of each page is enough: names are fixed per publication.

{% set known = [] %}
{% for names in var('nps_columns').values() %}{% do known.extend(names) %}{% endfor %}

select distinct r.publication_id, k.column_name
from {{ source('collected_raw', 'nps_workplace_record') }} as r
inner join {{ source('collected_meta', 'publication') }} as p using (publication_id)
cross join unnest(json_keys(r.record)) as k (column_name)
where p.status = 'complete'
  and r.row_no = 1
  and k.column_name not in (
    {%- for name in known %}'{{ name }}'{{ "," if not loop.last }}{% endfor -%}
  )
