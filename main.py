El error persiste porque, aunque en la función `create_google_event` estás consultando la base de datos para obtener `duracion_cita`, **el prompt que se envía a Retell AI no contiene esta variable**, por lo que el asistente no sabe que debe ajustar sus cálculos (o simplemente no tiene la restricción).

En tu código actual, `build_custom_prompt` **no está recibiendo ni integrando el argumento `duracion_cita**` en el texto que lee la IA.

Aquí tienes la solución paso a paso para que el bot sea consciente de esa duración y el backend la aplique correctamente:

### 1. Actualiza `build_custom_prompt`

Tu función actual ignora el parámetro `duracion_cita`. Debes añadirlo para que el prompt incluya la instrucción lógica.

```python
def build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, calendar_email, idioma="es", 
                        datos_reserva="...", duracion_cita=30): # <--- Añade el argumento aquí
    # ... (código previo igual)
    
    return f"""
    ... (resto de tu prompt)
    
    **REGLA OBLIGATORIA DE DURACIÓN DE CITA:**
    - Todas las citas deben tener una duración exacta de {duracion_cita} minutos.
    - Al confirmar la fecha y hora, no necesitas calcular el fin, pero ten en cuenta que el bloque reservado será siempre de {duracion_cita} minutos.
    
    ...
    """

```

### 2. Corrige la llamada en `create_bot_for_client`

En la línea donde llamas a `build_custom_prompt` dentro de `create_bot_for_client`, estás omitiendo el parámetro `duracion_cita`.

```python
def create_bot_for_client(nombre_negocio, sector, servicios, horario, zona, voice_id, calendar_email, 
                          idioma="es", datos_reserva="...", duracion_cita=30):
    # CORRECCIÓN AQUÍ: Pasa duracion_cita al constructor del prompt
    custom_prompt = build_custom_prompt(nombre_negocio, sector, servicios, horario, zona, 
                                        calendar_email, idioma, datos_reserva, duracion_cita)
    # ...

```

### 3. Asegura el flujo en `/book-appointment`

Si el modelo LLM todavía intenta enviar duraciones arbitrarias, el backend debe ser el juez final. Asegúrate de que `create_google_event` sea llamado **sin confiar en el `end_time**` que viene del `request`.

En tu endpoint `/book-appointment`, ya estás llamando a `create_google_event` pasando `None` como `end_time`. **Esto es correcto**, siempre y cuando `create_google_event` obtenga el valor real de la BD.

**Revisa este punto crítico en `create_google_event`:**
En tu código, haces un `SELECT` a la tabla `asistentes` usando `LOWER(TRIM(google_calendar_email))`. Si el email que recibe la función tiene espacios o mayúsculas distintas al de la base de datos, el `row` será `None` y tu código cae en el **fallback de 30 minutos** (que es lo que te está ocurriendo si tienes 45 configurados).

**Añade este log para depurar:**

```python
# Dentro de create_google_event, justo después del SELECT
if not row:
    logger.error(f"DEBUG: No se encontró registro en BD para email: '{calendar_id}'")

```

### Resumen de acción:

1. Añade `duracion_cita` como argumento en `build_custom_prompt` e insértalo en el texto del prompt.
2. Asegúrate de pasar `duracion_cita` desde `create_bot_for_client` y `update-retell-bot` hacia `build_custom_prompt`.
3. **Verifica en los Logs de Render** (usando `logger.info`) si al crear la cita, la consulta a la base de datos realmente encuentra los 45 minutos o si está cayendo en el `else` (el fallback de 30 minutos).

Si la base de datos no está devolviendo los 45 minutos, el problema no es el asistente, sino que la query `WHERE LOWER(TRIM(google_calendar_email)) = LOWER(TRIM(%s))` no está encontrando coincidencia exacta. Prueba a imprimir el valor de `calendar_id` que llega a esa función para compararlo con lo que tienes en PostgreSQL.
