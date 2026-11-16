-- One row per source record of every completely collected publication,
-- with period-specific column names mapped to one schema and types applied.
-- Codes stay as text so leading zeros survive.
-- Columns are read and typed by nps_record_rows (src/company_signal/sql/functions.sql),
-- which the lookup also uses. The publication date comes from the constant map
-- of nps_published_on, not a join (see that macro for why).
-- depends_on: {{ source('collected_meta', 'publication') }}

select
    n.publication_id,
    {{ nps_published_on() }}[n.publication_id] as published_on,
    n.* exclude (publication_id)
from nps_record_rows('{{ ref("base_nps__page_record") }}') as n
