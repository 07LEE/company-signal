-- One row per source record of every completely collected publication,
-- with period-specific column names mapped to one schema and types applied.
-- Codes stay as text so leading zeros survive.
-- Columns are read and typed by nps_record_rows (src/company_signal/sql/functions.sql),
-- which the lookup also uses.

with publication as (

    select publication_id, title_date as published_on
    from {{ source('collected_meta', 'publication') }}
    where status = 'complete'

)

select n.publication_id, p.published_on, n.* exclude (publication_id)
from nps_record_rows('{{ ref("base_nps__page_record") }}') as n
inner join publication as p using (publication_id)
