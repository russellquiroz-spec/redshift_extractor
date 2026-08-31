-- Bono por ruta: cumplimiento v3 recortado a 3 CPGs + tabulador de brackets
-- Grano: no_semana, ruta_id
-- Conceptos que pagan: facturacion, dn (clientes), cpg1, cpg2, cpg3 (cada CPG = monto_dn_cpgs / 3)
-- Ojo: la columna dn_digital del catalogo ya es cuota de DN (clientes), solo se quedo con el nombre viejo
-- Tabulador: brackets 1-10 fijos (90%..115%); del 11 en adelante dinamicos y sin tope, +5% de cumplimiento por bracket
-- Rutas PVGDL: no entran al tabulador, cobran 0.8% (.008) de su venta total
with
cat_bracket_raw as (
    select * from (
        select row_number() over(partition by ruta, extract(week from fecha_inicio::date) order by fecha_inicio::timestamp desc) filtro,
               ruta ruta_id,
               extract(week from fecha_inicio::date) no_semana,
               date_trunc('week',fecha_inicio::date)::date fecha_ini,
               coalesce(lead(date_trunc('week',fecha_inicio::date)::date) over (partition by ruta order by fecha_inicio::date),'2400-12-31'::date) - 1 fecha_fin,
               -- cast robusto: solo convierte si el texto es numerico (descarta '', '#N/A', '#DIV/0!', etc.)
               case when cuota      ~ '^-?[0-9]+([.][0-9]+)?$' then cuota::numeric(18,2)      end cuota_venta,
               case when dn_digital ~ '^-?[0-9]+([.][0-9]+)?$' then dn_digital::numeric(10,2) end cuota_dn,
               case when cpg1_pct   ~ '^-?[0-9]+([.][0-9]+)?$' then cpg1_pct::numeric(10,2)   end cuota_dn_cpg1, cpg1_nombre,
               case when cpg2_pct   ~ '^-?[0-9]+([.][0-9]+)?$' then cpg2_pct::numeric(10,2)   end cuota_dn_cpg2, cpg2_nombre,
               case when cpg3_pct   ~ '^-?[0-9]+([.][0-9]+)?$' then cpg3_pct::numeric(10,2)   end cuota_dn_cpg3, cpg3_nombre
               -- v3 quedo con 3 CPGs: cpg4/cpg5/cpg6 ya no se evaluan ni pagan
          from "catalog".catalog_brackets_v3_historico
         where 5=5
               --and ruta = 'PVLIN001'
    ) a
    where filtro = 1
)
,cat_semanas as (
    select distinct date_trunc('week',a.fecha)::date   fecha_ini_semana,
           date_trunc('week',a.fecha)::date+6          fecha_fin_semana,
           extract(week from a.fecha)                  no_semana
      from reference_data.vw_calendario_sin_festivos a
     where 5=5
           -- por fecha, no por numero de semana: extract(week) se repite cada año y duplicaba el join
           and date_trunc('week',a.fecha)::date = date_trunc('week',current_date)::date - 7
)
,cat_bracket as (
    select s.no_semana,
           s.fecha_ini_semana,
           s.fecha_fin_semana,
           b.ruta_id,
           b.cuota_venta,
           b.cuota_dn,
           b.cuota_dn_cpg1, b.cpg1_nombre,
           b.cuota_dn_cpg2, b.cpg2_nombre,
           b.cuota_dn_cpg3, b.cpg3_nombre
      from cat_semanas s
           inner join cat_bracket_raw b on s.fecha_ini_semana between b.fecha_ini and b.fecha_fin
)
,cat_cpgs as (
              select distinct no_semana, ruta_id, 1 no_cpg, 'LABORATORIOS PISA' nombre_cpg from cat_bracket where coalesce(cpg1_nombre,'NaN') <> 'NaN'
    union all select distinct no_semana, ruta_id, 2 no_cpg, 'KIMBERLY-CLARK DE MEXICO' nombre_cpg from cat_bracket where coalesce(cpg2_nombre,'NaN') <> 'NaN'
    union all select distinct no_semana, ruta_id, 3 no_cpg, 'HERDEZ' nombre_cpg from cat_bracket where coalesce(cpg3_nombre,'NaN') <> 'NaN'
    )
,tx_real as (
      select no_semana,
             ruta_id
             ,sum(monto_venta) venta
             ,count(distinct case when fl_cpg1    then ns_id end) dn_cpg1
             ,count(distinct case when fl_cpg2    then ns_id end) dn_cpg2
             ,count(distinct case when fl_cpg3    then ns_id end) dn_cpg3
             ,count(distinct ns_id) dn_total
        from (
            select coalesce(s.no_semana,extract(week from v.fecha_entrega_mx::date)) no_semana,
                   coalesce(cth.ruta, v.ruta_preventa)::varchar ruta_id
                   ,(coalesce(p.no_cpg,0) = 1) fl_cpg1
                   ,(coalesce(p.no_cpg,0) = 2) fl_cpg2
                   ,(coalesce(p.no_cpg,0) = 3) fl_cpg3
                   ,v.monto_venta
                   ,v.ns_id
              from analytics.mv_pedidos_enriquecidos_2026_v2_unpivot v
                   left join catalog.cat_estructura_comercial_v3 cth on v.ns_id::text = cth.netsuite_id::text
                   left join cat_semanas s
                          on (   v.fecha_creacion_mx::date between s.fecha_ini_semana and s.fecha_fin_semana
                              or v.fecha_entrega_mx::date  between s.fecha_ini_semana+1 and s.fecha_fin_semana+1)
                             and v.fecha_entrega_mx::date <= s.fecha_fin_semana+1
                   left join cat_cpgs p on v.cpg_proveedor = p.nombre_cpg and coalesce(cth.ruta, v.ruta_preventa)::varchar = p.ruta_id
                                       and coalesce(s.no_semana,extract(week from v.fecha_entrega_mx::date)) = p.no_semana
             where 5=5
                   and coalesce(s.no_semana,extract(week from v.fecha_entrega_mx::date)) = extract(week from date_trunc('week',(current_date))-1)
                   and v.status_item in ('ENTREGADO','EN PROGRESO')
                   --and coalesce(cth.ruta, v.ruta_preventa)::varchar = 'PVLIN001'
             )
       group by 1,2
)
-- tabulador fijo brackets 1-10 (equivalente al DATATABLE de DAX)
,cat_bono_fijo as (
              select 1  bracket, 0.90::numeric(6,4) cumplimiento_min, 0.93::numeric(6,4) cumplimiento_max, 1530::numeric(12,2) monto_facturacion,  765/3::numeric(12,2) monto_dn_cpg1, 765/3::numeric(12,2) monto_dn_cpg2, 765/3::numeric(12,2) monto_dn_cpg3, 255::numeric(12,2) monto_dn
    union all select 2,          0.93,                                0.96,                                1584,                                   792/3,                              792/3,                              792/3,                              264
    union all select 3,          0.96,                                0.99,                                1638,                                   819/3,                              819/3,                              819/3,                              273
    union all select 4,          0.99,                                1.00,                                1692,                                   846/3,                              846/3,                              846/3,                              282
    union all select 5,          1.00,                                1.03,                                1800,                                   900/3,                              900/3,                              900/3,                              300
    union all select 6,          1.03,                                1.05,                                1890,                                   945/3,                              945/3,                              945/3,                              315
    union all select 7,          1.05,                                1.10,                                1980,                                   990/3,                              990/3,                              990/3,                              330
    union all select 8,          1.10,                                1.12,                                2070,                                  1035/3,                             1035/3,                             1035/3,                              345
    union all select 9,          1.12,                                1.15,                                2160,                                  1080/3,                             1080/3,                             1080/3,                              360
    union all select 10,         1.15,                                1.20,                                2250,                                  1125/3,                             1125/3,                             1125/3,                              375
)
-- grano largo (no_semana, ruta_id, concepto) + bracket alcanzado
select a.ruta_id,
       a.no_semana,
       a.cuota_venta,
       a.venta,
       a.cump_venta,
       round(case
        when a.cump_venta = 0 then 0
        when coalesce(a.ruta_id,'') like 'PVGDL%' and a.cump_venta <> 0 then a.venta * .008
        when a.cump_venta < 1.20 then bv.monto_facturacion
        -- bracket 11+ despejado: (cump - 1.15) * 20 = escalones de 5% arriba del 115%, cada uno vale +90
        when a.cump_venta >= 1.20 then 2250 + floor((a.cump_venta::numeric(12,6) - 1.15) * 20) * 90
        else 0
       end, 2) bono_ventas,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then null   -- GDL no cae en bracket, cobra % directo
        when a.cump_venta = 0 then 0
        when a.cump_venta < 1.20 then bv.bracket
        else 10 + floor((a.cump_venta::numeric(12,6) - 1.15) * 20)::bigint
       end bracket_ventas,
       a.cuota_dn_cpg1,
       a.dn_cpg1,
       a.cump_dn_cpg1,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then 0   -- GDL solo cobra el % de venta
        when a.cump_dn_cpg1 = 0 then 0
        when a.cump_dn_cpg1 < 1.20 then bd1.monto_dn_cpg1
        -- (1125 + (b-10)*45) / 3 = 375 + (b-10)*15
        else 375 + floor((a.cump_dn_cpg1::numeric(12,6) - 1.15) * 20) * 15
       end bono_dn_cpg1,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then null
        when a.cump_dn_cpg1 = 0 then 0
        when a.cump_dn_cpg1 < 1.20 then bd1.bracket
        else 10 + floor((a.cump_dn_cpg1::numeric(12,6) - 1.15) * 20)::bigint
       end bracket_dn_cpg1,
       a.cuota_dn_cpg2,
       a.dn_cpg2,
       a.cump_dn_cpg2,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then 0
        when a.cump_dn_cpg2 = 0 then 0
        when a.cump_dn_cpg2 < 1.20 then bd2.monto_dn_cpg2
        else 375 + floor((a.cump_dn_cpg2::numeric(12,6) - 1.15) * 20) * 15
       end bono_dn_cpg2,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then null
        when a.cump_dn_cpg2 = 0 then 0
        when a.cump_dn_cpg2 < 1.20 then bd2.bracket
        else 10 + floor((a.cump_dn_cpg2::numeric(12,6) - 1.15) * 20)::bigint
       end bracket_dn_cpg2,
       a.cuota_dn_cpg3,
       a.dn_cpg3,
       a.cump_dn_cpg3,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then 0
        when a.cump_dn_cpg3 = 0 then 0
        when a.cump_dn_cpg3 < 1.20 then bd3.monto_dn_cpg3
        else 375 + floor((a.cump_dn_cpg3::numeric(12,6) - 1.15) * 20) * 15
       end bono_dn_cpg3,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then null
        when a.cump_dn_cpg3 = 0 then 0
        when a.cump_dn_cpg3 < 1.20 then bd3.bracket
        else 10 + floor((a.cump_dn_cpg3::numeric(12,6) - 1.15) * 20)::bigint
       end bracket_dn_cpg3,
       a.cuota_dn,
       a.dn_total,
       a.cump_dn,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then 0
        when a.cump_dn = 0 then 0
        when a.cump_dn < 1.20 then bd.monto_dn
        else 375 + floor((a.cump_dn::numeric(12,6) - 1.15) * 20) * 15
       end bono_dn,
       case
        when coalesce(a.ruta_id,'') like 'PVGDL%' then null
        when a.cump_dn = 0 then 0
        when a.cump_dn < 1.20 then bd.bracket
        else 10 + floor((a.cump_dn::numeric(12,6) - 1.15) * 20)::bigint
       end bracket_dn,
       a.cump_total,
       round(bono_ventas + bono_dn + bono_dn_cpg1 + bono_dn_cpg2 + bono_dn_cpg3, 2) bono_total
  from (
	    SELECT coalesce(c.ruta_id,v.ruta_id) ruta_id,
	           coalesce(c.no_semana,v.no_semana) no_semana,
	           coalesce(c.cuota_venta,0) cuota_venta,
	           COALESCE(v.venta, 0) venta,
	           case when coalesce(v.venta, 0) / nullif(c.cuota_venta, 0) >= .9 then coalesce(v.venta, 0) / nullif(c.cuota_venta, 0) else 0 end cump_venta,
	           coalesce(c.cuota_dn_cpg1,0) cuota_dn_cpg1,
	           coalesce(v.dn_cpg1,0) dn_cpg1,
	           case when coalesce(v.dn_cpg1, 0) / nullif(c.cuota_dn_cpg1, 0) >= .9 then coalesce(v.dn_cpg1, 0) / nullif(c.cuota_dn_cpg1, 0) else 0 end cump_dn_cpg1,
	           coalesce(c.cuota_dn_cpg2,0) cuota_dn_cpg2,
	           coalesce(v.dn_cpg2,0) dn_cpg2,
	           case when coalesce(v.dn_cpg2, 0) / nullif(c.cuota_dn_cpg2, 0) >= .9 then coalesce(v.dn_cpg2, 0) / nullif(c.cuota_dn_cpg2, 0) else 0 end cump_dn_cpg2,
	           coalesce(c.cuota_dn_cpg3,0) cuota_dn_cpg3,
	           coalesce(v.dn_cpg3,0) dn_cpg3,
	           case when coalesce(v.dn_cpg3, 0) / nullif(c.cuota_dn_cpg3, 0) >= .9 then coalesce(v.dn_cpg3, 0) / nullif(c.cuota_dn_cpg3, 0) else 0 end cump_dn_cpg3,
	           coalesce(c.cuota_dn,0) cuota_dn,
	           coalesce(v.dn_total,0) dn_total,
	           case when coalesce(v.dn_total, 0) / nullif(c.cuota_dn, 0) >= .9 then coalesce(v.dn_total, 0) / nullif(c.cuota_dn, 0) else 0 end cump_dn,
	           case
	              when coalesce(c.ruta_id,v.ruta_id) like 'PVGDL%'then cump_venta
	              else (cump_venta * .6) + (cump_dn_cpg1 * .1) + (cump_dn_cpg2 * .1) + (cump_dn_cpg3 * .1) + (cump_dn * .1)
	           end cump_total
	      FROM cat_bracket c
	           full join tx_real v ON v.ruta_id = c.ruta_id AND v.no_semana = c.no_semana
           ) a
    left join cat_bono_fijo bv on a.cump_venta >= bv.cumplimiento_min and a.cump_venta < bv.cumplimiento_max
    left join cat_bono_fijo bd on a.cump_dn >= bd.cumplimiento_min and a.cump_dn < bd.cumplimiento_max
    left join cat_bono_fijo bd1 on a.cump_dn_cpg1 >= bd1.cumplimiento_min and a.cump_dn_cpg1 < bd1.cumplimiento_max
    left join cat_bono_fijo bd2 on a.cump_dn_cpg2 >= bd2.cumplimiento_min and a.cump_dn_cpg2 < bd2.cumplimiento_max
    left join cat_bono_fijo bd3 on a.cump_dn_cpg3 >= bd3.cumplimiento_min and a.cump_dn_cpg3 < bd3.cumplimiento_max;
