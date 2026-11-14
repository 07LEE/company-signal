-- One row per workplace and base month.
--
-- The source has no workplace key, and only the first 6 digits of the business
-- registration number are published. The key is built from those 6 digits, the
-- normalized name, the legal-dong code and the date the workplace joined the
-- pension scheme. Source rows that still share a key in the same month (about
-- 0.01% of rows) are units the data cannot tell apart; they are summed and
-- counted in source_row_count.

with selected as (

    select r.*
    from {{ ref('stg_nps__workplace_record') }} as r
    inner join {{ ref('int_nps__selected_publication') }} using (publication_id, base_ym)

),

deduplicated as (

    -- Rows identical in every field are the same record received twice.
    select distinct * exclude (page, row_no)
    from selected

),

keyed as (

    select
        *,
        normalize_name(workplace_name) as name_norm
    from deduplicated

)

select
    md5(concat_ws('|', biz_reg_no6, name_norm, legal_dong_code, coalesce(applied_on::varchar, ''))) as workplace_id,
    base_ym,
    arg_max(workplace_name, subscriber_count) as workplace_name,
    name_norm,
    biz_reg_no6,
    legal_dong_code,
    applied_on,
    min(join_status_code) as join_status_code,
    arg_max(workplace_type_code, subscriber_count) as workplace_type_code,
    arg_max(road_address, subscriber_count) as road_address,
    arg_max(lot_address, subscriber_count) as lot_address,
    arg_max(industry_code, subscriber_count) as industry_code,
    arg_max(industry_name, subscriber_count) as industry_name,
    max(withdrawn_on) as withdrawn_on,
    sum(subscriber_count)::integer as subscriber_count,
    sum(billed_amount)::bigint as billed_amount,
    sum(new_subscriber_count)::integer as new_subscriber_count,
    sum(lost_subscriber_count)::integer as lost_subscriber_count,
    count(*)::integer as source_row_count
from keyed
group by base_ym, biz_reg_no6, name_norm, legal_dong_code, applied_on
