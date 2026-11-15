-- One publication per base month. The base month comes from the records, not
-- from the publication title. When a month was published more than once, the
-- latest publication wins; the others stay in raw only.

with per_publication as (

    select publication_id, published_on, base_ym, count(*) as row_count
    from {{ ref('stg_nps__workplace_record') }}
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
where pick_order = 1
