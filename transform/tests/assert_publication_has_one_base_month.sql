-- Every record of a publication must carry the same base month.

select publication_id
from {{ ref('stg_nps__workplace_record') }}
group by publication_id
having count(distinct base_ym) > 1
