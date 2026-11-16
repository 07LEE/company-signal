-- Fails when a publication uses a column name that is not in the mapping
-- (src/company_signal/sql/nps_columns.json). The first record of each
-- publication's first page is enough: names are fixed per publication, and
-- reading one file per publication keeps the test from reading every file again.

select distinct r.publication_id, k.column_name
from nps_file_records('{{ nps_page_files('page-000001') }}') as r
inner join {{ source('collected_meta', 'publication') }} as p using (publication_id)
cross join unnest(json_keys(r.record)) as k (column_name)
where p.status = 'complete'
  and r.row_no = 1
  and not nps_known_column(k.column_name)
