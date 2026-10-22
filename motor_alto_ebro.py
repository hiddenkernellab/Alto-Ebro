#!/usr/bin/env python3
"""Motor experimental de previsión por presa para Quintanilla, Cereceda y Cillaperlata.
Requiere CSV SAIH con timestamps originales y entradas de presa medidas o estimadas por balance.
No es un sistema de alerta ni una herramienta aprobada para operación hidráulica.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls

STEP_MIN = 15
POINTS_PER_H = 4
# Topología de estudio: solo señales de ramas independientes; verificar ubicación exacta
# y continuidad hidráulica/regulación antes de cualquier aplicación real.
PRESAS = {
    'quintanilla': {
        'nombre': 'Quintanilla Escalada', 'central': 'Quintanilla Escalada',
        'objetivo': 'Q_QUINTANILLA', 'aforos': {'A026': 'Ebro · Arroyo'},
        'excluidos': ['A279', 'A093', 'A092', 'A161', 'A283', 'A254'],
        'descripcion': 'A026 integra las aportaciones anteriores a Arroyo; sin sumar aforadores del mismo eje.'},
    'cereceda': {
        'nombre': 'Cereceda', 'central': 'Trespaderne',
        'objetivo': 'Q_CERECEDA', 'aforos': {'A026': 'Ebro · Arroyo', 'A279': 'Rudrón · Valdelateja'},
        'excluidos': ['A093', 'A092', 'A161', 'A283', 'A254'],
        'descripcion': 'Ebro y Rudrón como ramas distintas; el Oca y Nela confluyen aguas abajo de la presa.'},
    'cillaperlata': {
        'nombre': 'Cillaperlata', 'central': 'Quintana Martín Galíndez',
        'objetivo': 'Q_CILLAPERLATA',
        'aforos': {'A026': 'Ebro · Arroyo', 'A279': 'Rudrón · Valdelateja',
                   'A093': 'Oca · Oña', 'A092': 'Nela · Trespaderne'},
        'excluidos': ['A166', 'A161', 'A283', 'A254'],
        'descripcion': 'Jerea excluido: confluye después de la presa según indicación local. Nela A092 ya incluye Trueba y Nela A283. A161 es referencia aguas abajo, no otro sumando.'}
}


def load_csv(path):
    try:
        d = pd.read_csv(path, sep=None, engine='python', encoding='utf-8-sig', dtype=str)
    except Exception as e:
        raise ValueError('No se pudo leer el CSV: ' + str(e)) from e
    d.columns = [x.strip() for x in d.columns]
    if 'fecha' not in d:
        raise ValueError('Hace falta columna fecha ISO 8601 con zona horaria')
    times = pd.to_datetime(d.pop('fecha'), utc=True, errors='coerce')
    if times.isna().any(): raise ValueError('Hay fechas no válidas')
    if times.duplicated().any(): raise ValueError('Fechas repetidas: resolverlas antes de ajustar')
    d.index = pd.DatetimeIndex(times)
    d = d.sort_index()
    for name in d.columns:
        d[name] = pd.to_numeric(d[name].str.strip().str.replace(',', '.', regex=False),errors='coerce')
        d.loc[d[name] < 0,name] = np.nan
    if len(d)<2: raise ValueError('Serie demasiado corta')
    if any((d.index.view('int64') % (STEP_MIN * 60 * 10**9)) != 0):
        raise ValueError('Las fechas deben estar en cuartos de hora UTC')
    # Reindexar sin interpolar: los huecos no contienen información.
    return d.asfreq('15min')


def shifted_inputs(data, sources, lags):
    # Un instante en el destino se calcula con el aforo en un instante anterior.
    # media de 1h reduce ruido en el entrenamiento, siempre con observaciones del pasado.
    return pd.DataFrame({s: data[s].shift(int(lags[s])).rolling(4,min_periods=4).mean()
                         for s in sources},index=data.index)


def fit_positive(x, y, minimum=400):
    ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
    if ok.sum()<minimum: return None
    a=np.column_stack((x[ok], np.ones(int(ok.sum()))))
    coeff, _=nnls(a,y[ok])
    pred=a@coeff
    return coeff, float(np.mean((pred-y[ok])**2)),int(ok.sum())


def learn_lags(train, stations, target, max_lag_h=36):
    # Señal de cambio frente al objetivo para primera aproximación.
    lags={}
    dt=train[target].diff(4)
    for s in stations:
        ds=train[s].diff(4)
        scores=[]
        for lag in range(0,max_lag_h*4+1,4):
            x=pd.concat([dt,ds.shift(lag)],axis=1).dropna()
            if len(x)>100 and x.iloc[:,0].std()>1e-8 and x.iloc[:,1].std()>1e-8:
                corr=x.iloc[:,0].corr(x.iloc[:,1]);
                if np.isfinite(corr): scores.append((float(corr),lag))
        lags[s]=max(scores,default=(-999,0))[1]
    # Optimizar en conjunto respetando el conjunto de entrenamiento.
    for stage in range(2):
        for s in stations:
            around=(range(0,max_lag_h*4+1,4) if stage==0 else
                    range(max(0,lags[s]-12),min(max_lag_h*4,lags[s]+12)+1))
            best=(float('inf'),lags[s])
            for candidate in around:
                proposal={**lags,s:candidate}
                X=shifted_inputs(train,stations,proposal)
                ans=fit_positive(X.to_numpy(),train[target].to_numpy())
                if ans and ans[1]<best[0]:best=(ans[1],candidate)
            lags[s]=best[1]
    return lags


def evaluate(y, q):
    ok=np.isfinite(y)&np.isfinite(q)
    if ok.sum()<96*4:return {'estado':'datos_insuficientes','muestras':int(ok.sum())}
    a,b=y[ok],q[ok]
    metric={'estado':'evaluado','muestras':int(ok.sum()),
            'mae_m3s':round(float(np.mean(np.abs(a-b))),2),
            'rmse_m3s':round(float(np.sqrt(np.mean((a-b)**2))),2),
            'sesgo_m3s':round(float(np.mean(b-a)),2)}
    # Reportar también el error relativo en altos caudales del período de validación.
    th=np.quantile(a,0.9); high=(a>=th)
    metric['mae_decil_superior_m3s']=round(float(np.mean(np.abs(a[high]-b[high]))),2)
    return metric


def train_one(frame, dam, max_lag_h):
    config=PRESAS[dam];stations=list(config['aforos']); target=config['objetivo']
    missing=[s for s in [*stations,target] if s not in frame.columns]
    if missing:return {'presa':dam,'estado':'sin_entrenar','motivo':'Faltan columnas: '+', '.join(missing)}
    valid=frame[[*stations,target]]
    if len(valid)< 28*96:
        return {'presa':dam,'estado':'sin_entrenar','motivo':'Se requieren al menos 28 días de registros cada 15 minutos'}
    # El período de validación es el más reciente, sin modificar los coeficientes en él.
    split=int(len(valid)*.75)
    gap=max_lag_h*4 + 8
    if split-gap<14*96 or len(valid)-split<7*96:
        return {'presa':dam,'estado':'sin_entrenar','motivo':'No queda suficiente entrenamiento/validación'}
    train=valid.iloc[:split-gap]
    lags=learn_lags(train,stations,target,max_lag_h)
    x=shifted_inputs(train,stations,lags)
    fitted=fit_positive(x.to_numpy(),train[target].to_numpy())
    if fitted is None:return {'presa':dam,'estado':'sin_entrenar','motivo':'No hay suficientes valores simultáneos sin huecos'}
    coef,_,samples=fitted
    X=shifted_inputs(valid,stations,lags).iloc[split:].to_numpy()
    pred=np.full(len(X),np.nan)
    good=np.isfinite(X).all(axis=1)
    pred[good]=X[good]@coef[:-1]+coef[-1]
    met=evaluate(valid[target].iloc[split:].to_numpy(),pred)
    # Requisitos de seguridad del producto: no emitir si datos escasos de crecida.
    return {'presa':dam,'nombre':config['nombre'],'central':config['central'],
            'objetivo':target,'estado':'experimental_no_validado',
            'fuentes':config['aforos'],'retardo_intervalos':lags,
            'retardo_horas':{s:round(k*.25,2) for s,k in lags.items()},
            'coeficientes':{s:round(float(coef[i]),7) for i,s in enumerate(stations)},
            'base_m3s':round(float(coef[-1]),4),
            'entrenamiento_muestras':samples,
            'validacion':met,
            'comentario':'Retardos estadísticos variables en las crecidas; no equivalen a tiempos de viaje invariables.'}


def train_all(args):
    frame=load_csv(args.csv)
    outputs={d:train_one(frame,d,args.max_retardo_h) for d in PRESAS}
    model={'tipo':'modelo_de_investigacion_no_operativo','version':2,
           'entrenado_utc':datetime.now(timezone.utc).isoformat(),
           'base_temporal':'15min UTC','modelos':outputs}
    Path(args.salida).write_text(json.dumps(model,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for d,m in outputs.items():
        print(f'{d}: {m["estado"]} | {m.get("retardo_horas",m.get("motivo"))}')
    print('Modelos guardados en',args.salida)


def single_value(frame, station, when):
    # Evitar imputar huecos y usar únicamente lecturas realmente observadas.
    # Necesitamos los 4 puntos previos al paso de la onda por estación.
    times=pd.date_range(end=when,periods=4,freq='15min')
    values=frame[station].reindex(times)
    if values.isna().any():return None
    return float(values.mean())


def estimate_all(args):
    frame=load_csv(args.csv)
    store=json.loads(Path(args.modelo).read_text(encoding='utf-8'))['modelos']
    now=frame.index[-1]
    if now.to_pydatetime() > datetime.now(timezone.utc):
        raise ValueError('La última lectura no puede estar en el futuro')
    age_h=(datetime.now(timezone.utc)-now.to_pydatetime()).total_seconds()/3600
    if age_h>1.0 and not args.reproduccion_historica:
        raise ValueError(f'Los datos tienen {age_h:.1f} horas: no se publicará como predicción actual')
    rows=[]
    for dam,cfg in PRESAS.items():
        model=store.get(dam,{})
        for h in args.horas:
            moment=now+pd.Timedelta(hours=h)
            row={'presa':cfg['nombre'],'central':cfg['central'], 'hora_destino_utc':moment.isoformat(),
                 'horas_desde_lectura':float(h),'caudal_estimado_m3s':None,
                 'caudal_estimado_l_s':None,'estado':'sin_modelo_calibrado',
                 'aforos_utilizados':[], 'detalle_aportaciones':{}}
            if model.get('estado')!='experimental_no_validado':
                rows.append(row);continue
            codes=list(cfg['aforos'])
            if any(s not in frame for s in codes):
                row['estado']='faltan_aforos';rows.append(row);continue
            terms={};missing=[]
            for code in codes:
                when=moment-pd.Timedelta(minutes=15*model['retardo_intervalos'][code])
                q=single_value(frame,code,when)
                if q is None: missing.append(code);continue
                terms[code]={'caudal_aforo_m3s':round(q,3),'hora_aforo_utc':when.isoformat(),
                             'retardo_h':model['retardo_horas'][code],
                             'contribucion_estadistica_m3s':round(q*model['coeficientes'][code],3)}
            row['detalle_aportaciones']=terms
            row['aforos_utilizados']=list(terms)
            if missing:
                row['estado']='sin_datos_para_horizonte:'+','.join(missing)
            elif model.get('validacion',{}).get('estado')!='evaluado':
                row['estado']='validacion_insuficiente'
            else:
                q=max(0.,sum(v['contribucion_estadistica_m3s'] for v in terms.values())+model['base_m3s'])
                row.update(caudal_estimado_m3s=round(q,2),caudal_estimado_l_s=round(q*1000),
                           estado='experimental_no_validado')
            rows.append(row)
    result={'tipo':'PREVISION_EXPERIMENTAL_NO_OPERATIVA','creado_utc':datetime.now(timezone.utc).isoformat(),
            'ultima_lectura_utc':now.isoformat(),'datos_retraso_h':round(age_h,2),
            'aviso':'Prototipo no validado para operación ni alertas. No proporciona pronóstico cuando faltan entradas aguas arriba.',
            'predicciones':rows}
    Path(args.salida).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Resultado escrito en',args.salida)
    for dam in PRESAS:
        sub=[r for r in rows if r['central']==PRESAS[dam]['central']]
        print(PRESAS[dam]['nombre'],'|', ', '.join(f'{r["horas_desde_lectura"]}h: '+(str(r['caudal_estimado_m3s'])+' m³/s' if r['caudal_estimado_m3s'] is not None else r['estado']) for r in sub))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_subparsers(dest='accion',required=True)
    fit=s.add_parser('entrenar',help='Aprende retardos con históricos y caudales de entrada de presas')
    fit.add_argument('csv');fit.add_argument('--salida',default='modelos.json');fit.add_argument('--max-retardo-h',type=int,default=36)
    fit.set_defaults(func=train_all)
    pred=s.add_parser('prever',help='Calcula una hora destino por presa con caudales aguas arriba')
    pred.add_argument('csv');pred.add_argument('--modelo',default='modelos.json');pred.add_argument('--salida',default='predicciones.json')
    pred.add_argument('--horas',nargs='+',type=float,default=[0,1,2,3,5,8,12,24])
    pred.add_argument('--reproduccion-historica',action='store_true')
    pred.set_defaults(func=estimate_all)
    opts=p.parse_args()
    try: opts.func(opts)
    except (ValueError,KeyError,FileNotFoundError) as e: p.error(str(e))

if __name__=='__main__':main()
