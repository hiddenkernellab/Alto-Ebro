# Riadas Alto Ebro — aplicación web instalable (v0.3)

Panel experimental de consulta de caudales y lluvia para **Quintanilla Escalada**, **Cereceda** y **Cillaperlata**. No está homologado como alerta ni como soporte de maniobras en presas.

## Publicación rápida

1. Crea un repositorio **público y separado** en https://github.com/new llamado, por ejemplo, `riadas-alto-ebro`.
2. Sube **todo el contenido de esta carpeta**, conservando `index.html`, `icons/`, `data/` y **`.github/workflows/actualizar-saih.yml`** en sus rutas. Para conservar carpetas y la carpeta oculta `.github`, lo más cómodo es GitHub Desktop.
3. En **Settings → Pages → Build and deployment → Source** selecciona **GitHub Actions**.
4. Comprueba en **Settings → Actions → General → Workflow permissions** que el workflow puede escribir en el repositorio.
5. Abre **Actions → Actualizar SAIH y publicar panel → Run workflow**.
6. Tras la publicación, abre `https://TU_USUARIO.github.io/riadas-alto-ebro/`.
7. En Chrome Android, menú **⋮ → Instalar aplicación** (o **Añadir a pantalla de inicio**).

Los cron de GitHub Actions solicitan ejecuciones a los minutos 07 y 37 de cada hora, pero **GitHub no garantiza puntualidad ni continuidad**. El panel muestra la hora de obtención y oculta datos antiguos como actuales.

## Probar sin publicar

Desde el ordenador, en esta carpeta: `python -m http.server 8000` y abre `http://localhost:8000/`. La lluvia (Open-Meteo) se puede consultar, pero **los datos SAIH automáticos comienzan cuando la acción haya ejecutado `update_saih.py`**. Para una prueba aislada del lector en un ordenador con Internet: `python update_saih.py`, luego recarga la web.

## Datos y límites

- SAIH Ebro (CHE): fichas públicas de aforos. La acción recoge solo lecturas fechadas con menos de 2 h. Conserva observaciones realmente obtenidas; no reconstruye el histórico previo. En meses anteriores reduce la resolución a una muestra/hora.
- Open-Meteo: pronóstico de precipitación en puntos representativos (no precipitación areal de cuenca).
- Los archivos CSV se leen en el navegador y no se suben al repositorio.
- Jerea excluido de Cillaperlata conforme al usuario. Nela Villarcayo anidado en Nela Trespaderne; Palazuelos aguas abajo, no sumarlos.
- Las **horas de llegada** no se calculan automáticamente, porque faltan distancias de cauce comprobadas y celeridades obtenidas de series de crecidas. El formulario disponible es exclusivamente para escenarios manuales no calibrados.
- El sitio y sus lecturas serán públicos si se publica en GitHub Pages. **No introducir datos internos de Iberdrola**.
- Al usar estos datos se deben respetar las condiciones de uso y atribuir SAIH Ebro / CHE y Open-Meteo (CC BY 4.0).
