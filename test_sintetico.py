"""Prueba del motor con datos INVENTADOS, solo para comprobar la lógica matemática."""
import numpy as np
import pandas as pd
from motor_alto_ebro import PRESAS
from prediccion_nivel import generate

def main():
    now = pd.Timestamp.now(tz='UTC').floor('15min')
    idx = pd.date_range(end=now, periods=96, freq='15min')
    frame = pd.DataFrame({
        'A026':10.0,'A279':3.0,'A093':2.0,'A092':1.0,
        'COTA_CERECEDA':100+np.arange(len(idx))*.001}, index=idx)
    models={key:{'estado':'experimental_no_validado','validacion':{'estado':'evaluado'},
            'retardo_intervalos':{station:8 for station in cfg['aforos']},
            'coeficientes':{station:1 for station in cfg['aforos']}, 'base_m3s':0}
            for key,cfg in PRESAS.items()}
    curves={'cereceda':(np.array([99.,101.]),np.array([0.,2_000_000.]))}
    schedule=pd.DataFrame({'fecha':[now-pd.Timedelta(hours=1),now+pd.Timedelta(hours=24)],
                           'presa':['cereceda','cereceda'],'q_salida_m3s':[8.,8.]})
    out=generate(frame,models,curves,schedule,horizon_h=4)
    points=out['presas']['cereceda']['estimaciones']
    assert len(points)==25
    assert points[1]['minutos']==10 and points[1]['q_entrada_m3s'] is not None
    assert points[1]['cota_m'] is not None and points[1]['cota_m']>points[0]['cota_m']
    assert points[-1]['q_entrada_m3s'] is None and points[-1]['cota_m'] is None
    print('PRUEBA SUPERADA: 10 min, balance volumétrico y valores nulos fuera del horizonte.')
    print('ATENCION: los valores de la prueba son sintéticos, NO corresponden al Alto Ebro.')
if __name__=='__main__':main()
