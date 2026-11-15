-- One row per source record of every completely collected publication,
-- with period-specific column names mapped to one schema and types applied.
-- Codes stay as text so leading zeros survive.

with publication as (

    select publication_id, title_date as published_on
    from {{ source('collected_meta', 'publication') }}
    where status = 'complete'

)

select
    r.publication_id,
    p.published_on,
    r.page,
    r.row_no,
    try_strptime({{ src_col('base_ym') }}, ['%Y-%m', '%Y%m'])::date as base_ym,
    {{ src_col('workplace_name') }} as workplace_name,
    lpad({{ src_col('biz_reg_no') }}, 6, '0') as biz_reg_no6,
    {{ src_col('join_status_code') }} as join_status_code,
    {{ src_col('workplace_type_code') }} as workplace_type_code,
    {{ src_col('zip_code') }} as zip_code,
    {{ src_col('lot_address') }} as lot_address,
    {{ src_col('road_address') }} as road_address,
    lpad({{ src_col('legal_dong_code') }}, 10, '0') as legal_dong_code,
    lpad({{ src_col('admin_dong_code') }}, 10, '0') as admin_dong_code,
    {{ src_col('sido_code') }} as sido_code,
    {{ src_col('sigungu_code') }} as sigungu_code,
    {{ src_col('emd_code') }} as emd_code,
    lpad({{ src_col('industry_code') }}, 6, '0') as industry_code,
    {{ src_col('industry_name') }} as industry_name,
    try_strptime({{ src_col('applied_on') }}, ['%Y-%m-%d', '%Y%m%d'])::date as applied_on,
    try_strptime({{ src_col('rejoined_on') }}, ['%Y-%m-%d', '%Y%m%d'])::date as rejoined_on,
    try_strptime({{ src_col('withdrawn_on') }}, ['%Y-%m-%d', '%Y%m%d'])::date as withdrawn_on,
    ({{ src_col('subscriber_count') }})::integer as subscriber_count,
    ({{ src_col('billed_amount') }})::bigint as billed_amount,
    ({{ src_col('new_subscriber_count') }})::integer as new_subscriber_count,
    ({{ src_col('lost_subscriber_count') }})::integer as lost_subscriber_count
from {{ source('collected_raw', 'nps_workplace_record') }} as r
inner join publication as p using (publication_id)
