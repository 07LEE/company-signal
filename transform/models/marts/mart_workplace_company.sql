-- Workplaces tied to the corporation that disclosed figures about itself. The
-- tying rule is in fsc_ties (macros/create_functions.sql), which the lookup also
-- uses: a workplace is tied only when exactly one corporation found by its name
-- has a registration number starting with the workplace's 6 digits.

select t.workplace_id, c.*
from fsc_ties('{{ ref("mart_workplace") }}', '{{ ref("stg_fsc__corp_outline") }}') as t
inner join {{ ref('int_fsc__corporation') }} as c using (crno)
