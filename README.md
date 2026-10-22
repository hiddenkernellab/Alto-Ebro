# Alto Ebro — Motor de caudal y cota, tres presas, cada 10 minutos

**Incluye Quintanilla Escalada, Cereceda y Cillaperlata** y mantiene el motor original de calibración `motor_alto_ebro.py`.

## Qué se puede hacer ahora

- Seleccionar una presa y mover una barra desde **ahora hasta +24 horas, en intervalos de 10 minutos**.
- Consultar caudal estimado en **m³/s** y **l/s**, nivel/cota en **m** y variación en **cm**.
- Consultar el cambio de cota realmente observado durante la última hora, **sin proyectarlo automáticamente al futuro**.
- Ver dos gráficas independientes (caudal y cota), sin mostrar números inventados si faltan datos.
- Calcular cota futura por balance de volumen **solo cuando existan** un caudal de entrada validado, el escenario de salidas, la cota inicial y una curva cota-volumen aprobada para esa presa.

No aporta lecturas actuales por Internet: el repositorio GitHub sigue sin lograr datos SAIH. **No hay modelos entrenados ni curvas reales de las presas en este paquete**; por tanto, al abrir el visor todavía no aparecerán previsiones reales. El motor está programado, no calibrado ni operativo.

## Por qué necesitamos la curva cota-volumen

La cota no depende únicamente de cuánto ha subido la última hora. Se calcula con la conservación del agua:

**Volumen futuro = volumen actual + ∫(Q de entrada − Q de salida) dt.**

La relación oficial **cota (m) ↔ volumen (m³)** permite convertir ese volumen en metros. El caudal de salida incluye, según corresponda, agua turbinada, vertida, desembalsada o derivada, evitando dobles cómputos. Las maniobras futuras generan escenarios distintos; la cota no puede predecirse sin especificar esas salidas.

El programa usa integración trapezoidal **cada 600 segundos** y no extrapola fuera de la curva suministrada. Para la hora de entrada en el aforador se mantiene el desplazamiento calibrado del motor anterior; los puntos a 10 minutos se interpolan entre cálculos soportados por observaciones de 15 minutos (no son nuevas observaciones oficiales).

## Entradas

1. `historicos.csv`: mismo formato que la versión anterior: columna `fecha` ISO-8601 con zona horaria, estaciones SAIH y columnas opcionales `COTA_QUINTANILLA`, `COTA_CERECEDA`, `COTA_CILLAPERLATA` (en metros), con el mismo intervalo de 15 min.
2. `modelos.json`: producto de `motor_alto_ebro.py entrenar`, solo se ajusta con caudales de entrada en las presas contrastados y validación temporal; sin datos suficientes se devuelve valor nulo.
3. `curvas_cota_volumen.csv`: columnas `presa,cota_m,volumen_m3`. `presa` = quintanilla / cereceda / cillaperlata. Introducir **curvas verificadas**, estrictamente crecientes (elevación referida al mismo datum y volumen en m³). No inventar curvas.
4. `salidas_plan.csv`: columnas `fecha,presa,q_salida_m3s` con escenarios/autorizaciones de salida, cubriendo todo el horizonte. La primera fecha debe ser anterior o igual a la última lectura; la última debe ser posterior o igual al final de la previsión. Entre registros el escenario mantiene el último caudal indicado; **no prolonga más allá del último registro**.

El archivo `curvas_cota_volumen.csv` incluido solo lleva cabeceras; lo mismo ocurre con el de salidas. Son plantillas, no datos reales.

## Cómo se utiliza

En Windows, con Python 3.10 o superior:

```bash
py -m pip install -r requirements.txt
py motor_alto_ebro.py entrenar historicos.csv --salida modelos.json
py prediccion_nivel.py historicos.csv --modelo modelos.json --curvas curvas_cota_volumen.csv --salidas salidas_plan.csv --salida prediccion_10min.json
```

Si solo quieres comprobar el estado sin curvas ni salidas, omite `--curvas` y `--salidas`; se muestra la cota observada y la tendencia, pero no una previsión futura de cota.

Abre `visor_10min.html` y selecciona el JSON generado. La barra tiene pasos de 10 minutos. **No subir datos internos o sensibles al repositorio público.** El visor procesa el JSON localmente sin enviarlo a ningún sitio.

## Limitaciones y validación

- El nivel en una presa puede cambiar por cambios de operación, compuertas, regulación, variación del volumen útil y aportaciones locales: una extrapolación lineal del tipo «sube 5 cm/h, luego subirá 50 cm en diez horas» no es válida para una crecida.
- Para prever +24 h hacen falta pronósticos hidrológicos/meteorológicos y escenarios operativos; los aforadores aguas arriba no siempre aportan tanto tiempo de anticipación.
- El modelo NO puede reemplazar al SAIH, a un sistema de telecontrol, a un modelo hidráulico calibrado ni a las instrucciones de operación de Iberdrola.
- Una previsión experimental de cota requiere pruebas con varios episodios independientes y análisis de error, especialmente para crecidas rápidas.
