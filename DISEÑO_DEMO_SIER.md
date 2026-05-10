# Diseño formal de la demo SIER-like

## 1. Objetivo pedagógico

La demo busca que los alumnos perciban que es difícil hacer política económica en tiempo real para maximizar apoyo político cuando:

- el tiempo es escaso
- otros países actúan simultáneamente
- las políticas tienen efectos cruzados
- los jugadores conocen la dirección de los efectos, pero no su magnitud exacta

## 2. Estructura institucional

- Un único **mundo** activo por partida
- Entre **2 y 10 países**
- Por cada país, dos cuentas:
  - **visualización**: solo observa
  - **líder**: envía políticas
- Una cuenta **profesor** con control total

## 3. Variables observables por los alumnos

Para todos los países, la app muestra:

- apoyo político
- inflación
- desempleo
- consumo
- gasto estatal
- exportaciones
- importaciones
- tipo de cambio nominal
- IVA
- empleo público
- arancel medio impuesto y recibido
- déficit estimado
- tarjeta vigente

También se muestra el historial de políticas pasadas de todos los países.

## 4. Instrumentos de política

En cada período, cada líder puede elegir:

- variación del tipo de cambio nominal
- variación del gasto estatal
- cambio del IVA
- variación del empleo público
- cambios discriminados de aranceles contra otros países

### Límites por período

- Tipo de cambio: máximo `±4%`
- Gasto estatal: máximo `±6%`
- IVA: máximo `±5` puntos porcentuales
- Empleo público: máximo `±20%`
- Arancel por socio: máximo `±2` puntos porcentuales, con piso en `0%`

No se permiten subsidios a las exportaciones, por lo que un arancel nunca puede quedar por debajo de cero.

## 5. Timing

La demo arranca por defecto con:

- `4` períodos
- `15` minutos por período

Ambos parámetros son modificables por el profesor.

El profesor puede:

- cerrar manualmente el período antes del tiempo límite
- habilitar avance automático si todos los países enviaron

## 6. Tarjetas

### Tarjeta amarilla

Se activa si ocurre cualquiera de las siguientes condiciones:

- inflación > 12
- déficit estimado > 8

### Tarjeta roja

Se activa si ocurre cualquiera de las siguientes condiciones:

- inflación > 18
- déficit estimado > 15

La tarjeta roja impone para el siguiente período una obligación automática:

- la variación del gasto debe ser menor o igual a `-4%`

El profesor además puede imponer manualmente esta restricción.

## 7. Función de apoyo político

Se utiliza una media simple de tres componentes **normalizados** para que el punto de partida ronde 100:

1. consumo
2. estabilidad de precios
3. empleo

La fórmula concreta es:

- `consumption_score = consumo`
- `inflation_score = 120 - 4 * |inflación|`
- `employment_score = 120 - 3 * desempleo`
- `apoyo = (consumption_score + inflation_score + employment_score) / 3`

Luego el apoyo se acota al rango razonable del tablero.

Esta normalización se hizo para respetar tu pedido de promedio simple entre consumo, inflación absoluta y desempleo, evitando que las escalas heterogéneas deformen el puntaje.

## 8. Motor económico reducido de la demo

El motor no pretende ser un modelo estructural realista. Es un sistema reducido, estable y pedagógico.

### 8.1 Estado inicial simétrico

Cada país arranca con:

- tipo de cambio = 100
- gasto = 20
- IVA = 18
- empleo público = 12
- índice de precios = 100
- inflación = 4
- desempleo = 8
- consumo = 100
- exportaciones = 20
- importaciones = 20
- apoyo político = 100
- aranceles bilaterales = 0

### 8.2 Competitividad externa

El tipo de cambio real se aproxima con el promedio simple frente al resto:

- `q_ij = (e_i / e_j) * (P_j / P_i)`
- `q_i = promedio_j(q_ij)`

Un aumento de `q_i` implica una depreciación real y mejora competitiva.

### 8.3 Exportaciones

Las exportaciones del país `i` crecen con:

- la demanda externa
- la mejora de competitividad real

Y caen con:

- los aranceles que otros países le aplican

### 8.4 Importaciones

Las importaciones del país `i` crecen con:

- la demanda interna

Y caen con:

- la mejora de competitividad real propia
- los aranceles que ese país decide imponer

### 8.5 Inflación

La inflación tiene persistencia leve y depende de:

- inflación pasada
- impulso de demanda
- cambio del IVA
- variación del tipo de cambio
- inflación externa promedio
- aranceles impuestos

### 8.6 Desempleo

El desempleo cae cuando la actividad mejora y cuando sube el empleo público.

También incorpora una penalización leve si la inflación supera niveles altos.

### 8.7 Consumo

El consumo mejora cuando:

- cae el desempleo
- mejora la demanda
- crecen las exportaciones

Y empeora cuando:

- sube la inflación
- sube el IVA

### 8.8 Déficit estimado

Se usa una medida simplificada:

- gasto fiscal = `0.75 * gasto estatal + 0.40 * empleo público`
- ingresos = recaudación de IVA + recaudación arancelaria
- déficit estimado = gasto fiscal - ingresos

No hay sector monetario ni reservas en esta demo.

## 9. Arquitectura técnica

### Frontend

- Streamlit

### Persistencia

- SQLite en modo WAL

### Sesiones

- autenticación interna con hash PBKDF2
- estado centralizado en base de datos
- el navegador no guarda el estado maestro del juego

### Lógica del tiempo

- el reloj oficial es una `deadline` del período guardada en base de datos
- las vistas actualizan el contador y disparan el cierre del período cuando corresponde

## 10. Alcance explícitamente excluido de esta demo

- Banco Central
- dinero
- tasa de interés
- reservas internacionales
- shocks exógenos programados
- exportación a Excel
- gráficos en tiempo real
- historial persistente entre partidas
- despliegue productivo endurecido

## 11. Evolución natural a versión 2

Si la demo funciona bien, la siguiente versión razonable sería:

- separar Tesoro y Banco Central
- incorporar reservas y restricciones externas
- agregar shocks
- sumar gráficos
- guardar partidas
- pasar de SQLite a PostgreSQL
- agregar autenticación más robusta
