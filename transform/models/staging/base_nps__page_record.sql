-- The records of every completely collected publication, read from the page
-- files exactly as the API sent them. The files are the only copy; the
-- collector does not load them into its database. A publication still being
-- collected has files in the same directory and is left out here, by the
-- constant map of nps_published_on rather than a join (see that macro for why).
-- depends_on: {{ source('collected_meta', 'publication') }}
{{ config(materialized='view') }}

select *
from nps_file_records('{{ nps_page_files() }}')
where map_contains({{ nps_published_on() }}, publication_id)
