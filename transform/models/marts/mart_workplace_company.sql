-- Workplaces tied to the corporation that disclosed figures about itself.
--
-- A workplace is asked about by its name. Among the corporations found, those
-- whose business registration number starts with the workplace's published 6
-- digits are candidates. Only a workplace with exactly one candidate is tied:
-- with two, showing either could show the wrong company.

with workplace as (

    select workplace_id, biz_reg_no6, fsc_query_name(name_norm) as query_name
    from {{ ref('mart_workplace') }}

),

candidate as (

    select distinct w.workplace_id, i.crno
    from workplace as w
    inner join {{ ref('stg_fsc__corp_outline') }} as i
        on i.query_name = w.query_name
        and left(i.bzno, 6) = w.biz_reg_no6

),

tied as (

    select workplace_id, any_value(crno) as crno
    from candidate
    group by workplace_id
    having count(*) = 1

)

select t.workplace_id, c.*
from tied as t
inner join {{ ref('int_fsc__corporation') }} as c using (crno)
