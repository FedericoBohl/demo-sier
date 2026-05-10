# Demo SIER-like en Streamlit

Esta es una demo jugable inspirada en la lógica pedagógica del SIER Game, adaptada a los requisitos definidos en la conversación.

## Qué incluye

- Un mundo con entre 2 y 10 países
- Una cuenta **profesor** (`admin`) que administra la partida
- Una cuenta **líder** por país que puede enviar políticas
- Una cuenta **visualización** por país que solo observa datos
- Cronómetro por período
- Cierre manual del período por parte del profesor
- Opción de avance automático si todos los países enviaron
- Motor macroeconómico reducido y oculto para la demo
- Tarjetas amarilla y roja por inflación o déficit
- Restricción automática de recorte de gasto tras tarjeta roja
- Historial de políticas enviadas y ranking final

## Credenciales iniciales

- Usuario: `admin`
- Contraseña: `admin123`

Desde el panel del profesor puedes crear un nuevo mundo y definir las credenciales de todos los países.

## Estructura de archivos

- `app.py`: interfaz Streamlit
- `storage.py`: base SQLite, autenticación, persistencia, cierre de período
- `game_engine.py`: motor económico reducido de la demo
- `.streamlit/config.toml`: configuración visual básica
- `requirements.txt`: dependencias mínimas
- `DISEÑO_DEMO_SIER.md`: diseño formal del juego y del motor económico

## Cómo correr la app

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Recomendación de uso

Para una clase real, conviene correr esta app en una sola computadora o servidor accesible por navegador para todos los alumnos. La demo usa SQLite, que para un MVP liviano multiusuario es suficiente, pero para una versión institucional más robusta convendría migrar luego a PostgreSQL.

## Notas de diseño

- La autenticación es interna a la demo y no usa OIDC.
- El mundo activo es único.
- Al crear un mundo nuevo, se eliminan partidas y cuentas de países previas.
- No hay exportación a Excel, gráficos en tiempo real ni historial persistente entre partidas, porque explícitamente se dejó fuera del alcance de la demo.


## Cambios de esta versión

- Los gráficos arrancan en el período 0
- El profesor puede ver en tiempo real las políticas ya enviadas del período en curso
- Los países siguen viendo las políticas ajenas recién al período siguiente
- La duración puede definirse por período mediante una lista separada por comas
- Se agregaron gráficos de apoyo político, demanda, variaciones, empleo y tipo de cambio real
- Se agregaron validaciones explícitas para IVA y aranceles finales
