-- The records of every completely collected publication, read from the page
-- files exactly as the API sent them. The files are the only copy; the
-- collector does not load them into its database. A publication still being
-- collected has files in the same directory and is left out here.
{{ config(materialized='view') }}

select r.*
from nps_file_records('{{ nps_page_files() }}') as r
inner join {{ source('collected_meta', 'publication') }} as p using (publication_id)
where p.status = 'complete'
