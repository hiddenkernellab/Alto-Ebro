#!/usr/bin/env python3
"""Motor experimental de caudal + cota a intervalos de 10 minutos.

No extrapola horas sin datos. No es utilizable para maniobras ni alertas.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from motor_alto_ebro import PRESAS, load_csv, single_value


COTA_COLUMN = {'quintanilla': 'COTA_QUINTANILLA',
               'cereceda': 'COTA_CERECEDA',
               'cillaperlata': 'COTA_CILLAPERLATA'}


def load_long_csv(path, required):
    df = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig')
    df.columns = df.columns.str.strip()
    miss = [c for c in required if c not in df.columns]
    if miss:
        raise ValueError(f'{path}: columnas ausentes: {miss}')
    df['fecha'] = pd.to_datetime(df['fecha'], utc=True, errors='coerce')
    if df['fecha'].isna().any():
        raise ValueError(f'{path}: fecha no válida')
    df['presa'] = df['presa'].astype(str).str.strip().str.lower()
    if not df['presa'].isin(PRESAS).all():
        raise ValueError(f'{path}: la presa debe ser quintanilla, cereceda o cillaperlata')
    if df.duplicated(['presa','fecha']).any():
        raise ValueError(f'{path}: registros repetidos de presa y fecha')
    return df.sort_values('fecha')


def load_curves(path):
    df = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig')
    df.columns = df.columns.str.strip()
    if not {'presa','cota_m','volumen_m3'}.issubset(df.columns):
        raise ValueError('curvas: columnas presa,cota_m,volumen_m3')
    df['presa'] = df['presa'].astype(str).str.strip().str.lower()
    curves={}
    for key,grp in df.groupby('presa'):
        if key not in PRESAS: raise ValueError('Presa no reconocida en curvas: '+key)
        curve=grp.sort_values('cota_m')
        cotas=curve['cota_m'].to_numpy(dtype=float)
        volumes=curve['volumen_m3'].to_numpy(dtype=float)
        if len(cotas)<2 or not np.isfinite(cotas).all() or not np.isfinite(volumes).all() or not (np.diff(cotas)>0).all() or not (np.diff(volumes)>0).all():
            raise ValueError('Curva cota-volumen no válida para '+key)
        curves[key]=(cotas,volumes)
    return curves


def interpolate_within(x, xp, fp):
    if not np.isfinite(x) or x<xp[0] or x>xp[-1]:
        return None
    return float(np.interp(x, xp, fp))


def input_flow_at(frame, m, cfg, target_time):
    if m.get('estado')!='experimental_no_validado' or m.get('validacion',{}).get('estado')!='evaluado':
        return None, 'modelo_sin_validacion'
    terms=[]
    for code in cfg['aforos']:
        if code not in frame.columns: return None, f'falta_columna:{code}'
        lag=m.get('retardo_intervalos',{}).get(code)
        factor=m.get('coeficientes',{}).get(code)
        if lag is None or factor is None: return None, f'falta_ajuste:{code}'
        at=target_time-pd.Timedelta(minutes=15*int(lag))
        q=single_value(frame,code,at)
        if q is None: return None, f'sin_lectura:{code}'
        terms.append(q*float(factor))
    q=max(0.0, sum(terms)+float(m.get('base_m3s',0)))
    return q,'experimental_no_validado'


def q_at_10min(frame, model, cfg, when):
    # Interpolación entre DOS cálculos del modelo sobre cuadrícula de 15 min,
    # únicamente si ambos son compatibles con observaciones existentes.
    left=when.floor('15min')
    right=when.ceil('15min')
    a,sa=input_flow_at(frame,model,cfg,left)
    if a is None:return None,sa
    if right==left:return a,sa
    b,sb=input_flow_at(frame,model,cfg,right)
    if b is None:return None,sb
    ratio=(when-left).total_seconds()/(right-left).total_seconds()
    return a+(b-a)*ratio,'experimental_interpolado_15min'


def most_recent_stage(frame, col, now):
    if col not in frame: return None, None, None
    s=frame[col].dropna()
    if s.empty: return None,None,None
    recent=s.loc[:now]
    if recent.empty: return None,None,None
    when=recent.index[-1]
    if (now-when).total_seconds()>20*60: return None,None,None
    current=float(recent.iloc[-1])
    # Tendencia por diferencia real, comparando cota actual con ~1h antes.
    start=when-pd.Timedelta(hours=1)
    before=recent.loc[:start]
    if before.empty or (start-before.index[-1]).total_seconds()>20*60:
        return current,None,when
    hours=(when-before.index[-1]).total_seconds()/3600
    if hours<=0:return current,None,when
    trend=(current-float(before.iloc[-1]))*100/hours
    return current,trend,when


def outgoing_at(schedules, dam, when):
    if schedules is None:return None
    df=schedules[schedules['presa']==dam]
    if df.empty or when<df['fecha'].iloc[0] or when>df['fecha'].iloc[-1]:return None
    past=df[df['fecha']<=when]
    if past.empty:return None
    return float(past['q_salida_m3s'].iloc[-1])


def generate(frame, store, curves=None, schedules=None, horizon_h=24, step_minutes=10, allow_historical=False):
    if 60%step_minutes!=0 or step_minutes<=0:
        raise ValueError('El intervalo debe dividir 60 minutos')
    now=frame.index[-1]
    age=(datetime.now(timezone.utc)-now.to_pydatetime()).total_seconds()/60
    if age < -5:
        raise ValueError('Las lecturas no pueden estar en el futuro')
    if age>60 and not allow_historical:
        raise ValueError('Lecturas caducadas: no se permite mostrar una predicción actual')
    result={'tipo':'EXPERIMENTAL_NO_OPERATIVO','origen_lecturas_utc':now.isoformat(),
            'generado_utc':datetime.now(timezone.utc).isoformat(),
            'intervalo_min':step_minutes,'horizonte_h':horizon_h,
            'advertencia':'Modelo no calibrado o no validado para decisiones de seguridad. Cota solo mediante balance de volumen e hipótesis de salidas.' ,
            'presas':{}}
    for dam,cfg in PRESAS.items():
        model=store.get(dam,{})
        current,trend,ct=most_recent_stage(frame,COTA_COLUMN[dam],now)
        curve=(curves or {}).get(dam)
        # Para iniciar un balance futuro, la cota debe corresponder al mismo instante
        # que las últimas lecturas; una cota anterior NO es volumen actual.
        cota_sincronizada = ct is not None and abs((now-ct).total_seconds()) <= 5*60
        storage = interpolate_within(current,*curve) if curve is not None and current is not None and cota_sincronizada else None
        q_prev=None
        rows=[]
        for minutes in range(0,int(horizon_h*60)+1,step_minutes):
            when=now+pd.Timedelta(minutes=minutes)
            q,status=q_at_10min(frame,model,cfg,when)
            record={'minutos':minutes,'fecha_utc':when.isoformat(),'q_entrada_m3s':round(q,3) if q is not None else None,
                    'q_entrada_l_s':round(q*1000) if q is not None else None,
                    'cota_m':None,'diferencia_cota_cm':None,
                    'estado_caudal':status,'estado_cota':'no_calculable'}
            if minutes==0:
                if current is not None:
                    record['cota_m']=round(current,4)
                    record['diferencia_cota_cm']=0
                    record['estado_cota']='cota_observada' if cota_sincronizada else 'ultima_cota_previa_no_sincronizada'
            elif storage is not None and q is not None and q_prev is not None:
                prev_when=when-pd.Timedelta(minutes=step_minutes)
                qout1=outgoing_at(schedules,dam,prev_when)
                qout2=outgoing_at(schedules,dam,when)
                if qout1 is not None and qout2 is not None:
                    # Balance dV/dt=(Qin-Qout), integración trapezoidal.
                    dt=step_minutes*60
                    delta=(q_prev+q-qout1-qout2)/2*dt
                    new_storage=storage+delta
                    new_cota=interpolate_within(new_storage,curve[1],curve[0])
                    if new_cota is not None:
                        storage=new_storage
                        record['cota_m']=round(new_cota,4)
                        record['diferencia_cota_cm']=round((new_cota-current)*100,1)
                        record['estado_cota']='experimental_balance_volumen'
                    else:
                        storage=None
                        record['estado_cota']='fuera_curva_cota_volumen'
                else:
                    storage=None
                    record['estado_cota']='falta_plan_caudal_salida'
            elif minutes>0:
                if curve is None:record['estado_cota']='falta_curva_cota_volumen'
                elif current is None:record['estado_cota']='falta_cota_actual'
                elif not cota_sincronizada:record['estado_cota']='cota_no_sincronizada_con_aforos'
                elif q is None or q_prev is None:record['estado_cota']='sin_prevision_entrada'
                else:record['estado_cota']='sin_datos_para_balance'
            rows.append(record)
            q_prev=q
        result['presas'][dam]={'nombre':cfg['nombre'],'central':cfg['central'],
                               'cota_observada_m':round(current,4) if current is not None else None,
                               'hora_cota_observada_utc':ct.isoformat() if ct is not None else None,
                               'tendencia_observada_cm_h':round(trend,2) if trend is not None else None,
                               'fuentes_entrada':cfg['aforos'],
                               'estimaciones':rows,
                               'comentario':'Cota futura depende de caudal entrante estimado, curva oficial cota-volumen y salidas planificadas/observadas.'}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv',help='Historico de 15 min con aforos y cotas, CSV de entrada original')
    parser.add_argument('--modelo',default='modelos.json')
    parser.add_argument('--curvas',help='CSV cota-volumen validado, por presa')
    parser.add_argument('--salidas',help='CSV de escenario de caudales de salida, por presa')
    parser.add_argument('--salida',default='prediccion_10min.json')
    parser.add_argument('--horas',type=int,default=24)
    parser.add_argument('--reproduccion-historica',action='store_true')
    args=parser.parse_args()
    try:
        if args.horas<1 or args.horas>72: raise ValueError('Horas debe estar entre 1 y 72')
        frame=load_csv(args.csv)
        store=json.loads(Path(args.modelo).read_text(encoding='utf-8'))['modelos']
        curves=load_curves(args.curvas) if args.curvas else None
        schedules=load_long_csv(args.salidas,['fecha','presa','q_salida_m3s']) if args.salidas else None
        if schedules is not None:
            schedules['q_salida_m3s']=pd.to_numeric(schedules['q_salida_m3s'],errors='coerce')
            if schedules['q_salida_m3s'].isna().any() or (schedules['q_salida_m3s']<0).any():
                raise ValueError('Caudales de salida no válidos')
        result=generate(frame,store,curves,schedules,args.horas,10,args.reproduccion_historica)
        Path(args.salida).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for dam,data in result['presas'].items():
            flow_avail=sum(x['q_entrada_m3s'] is not None for x in data['estimaciones'])
            stage_avail=sum(x['cota_m'] is not None for x in data['estimaciones'])
            print(f'{data["nombre"]}: caudal estimable {flow_avail}/{len(data["estimaciones"])} puntos; cota {stage_avail}/{len(data["estimaciones"])} puntos')
        print('Salida:',args.salida)
    except (ValueError,KeyError,FileNotFoundError) as exc:
        parser.error(str(exc))

if __name__=='__main__':main()
