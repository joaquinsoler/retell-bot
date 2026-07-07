<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Creador de Asistentes AI - Dansu</title>
    <style>
        /* === PANTALLA DE CARGA === */
        #preloader {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 99999;
            transition: opacity 0.6s ease;
        }
        .loader-content { text-align: center;
        }
        .spinner {
            width: 70px;
            height: 70px;
            border: 6px solid #f3f3f3;
            border-top: 6px solid #0078FF;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }
        @keyframes spin { to { transform: rotate(360deg);
        } }
        .loader-text { color: #334155; font-size: 15px; font-weight: 500;
        }

        /* Estilos originales + mejoras */
        html, body {
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            margin: 0; padding: 10px; background: #eaf2f8; color: #333;
            overflow: hidden;
        }
        h2, h3 { color: #111;
        }
        .subtitle { color: #666; font-size: 14px; margin-bottom: 20px;
        }

        /* Contenedor relativo para posicionar las flechas */
        .slider-wrapper-rel {
            position: relative;
            max-width: 100%;
            margin-bottom: 20px;
        }

        .slider-container {
            display: flex;
            overflow-x: auto; gap: 15px; padding: 10px 5px;
            scroll-snap-type: x mandatory; scrollbar-width: none;
            scroll-behavior: smooth;
        }
        .slider-container::-webkit-scrollbar { display: none;
        }

        /* Flechas elegantes de navegación */
        .slider-arrow {
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            width: 40px;
            height: 40px;
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid #e2e8f0;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            cursor: pointer;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            z-index: 10;
            transition: all 0.2s ease;
            user-select: none;
        }
        .slider-arrow:hover {
            background: #fff;
            border-color: #0078FF;
            color: #0078FF;
            box-shadow: 0 10px 15px -3px rgba(0, 120, 255, 0.1);
        }
        .slider-arrow.left { left: -10px;
        }
        .slider-arrow.right { right: -10px;
        }

        /* Ocultar flechas en móviles si se prefiere usar el gesto táctil nativo */
        @media (max-width: 600px) {
            .slider-arrow { display: none;
        }
        }

        .card { flex: 0 0 260px;
            border: 2px solid #e2e8f0; border-radius: 16px;
            background: #fff; scroll-snap-align: start;
        }
        .card.selected { border-color: #0078FF;
            box-shadow: 0 10px 15px rgba(0,120,255,0.2);
        }

        .video-wrapper { height: 160px; background: #000;
            border-top-left-radius: 14px; border-top-right-radius: 14px; overflow: hidden;
        }
        video { width: 100%; height: 100%;
            object-fit: cover;
        }

        .card-info { padding: 15px;
        }
        .card-name { font-size: 18px; font-weight: bold; margin-bottom: 12px;
        }

        .btn-select { width: 100%; padding: 10px; border: 2px solid #0078FF; background: transparent;
            color: #0078FF; border-radius: 8px; font-weight: bold; cursor: pointer; }
        .card.selected .btn-select { background: #0078FF;
            color: white; }

        .form-container, .success-box, .instructions-box, .confirmation-box {
            max-width: 500px;
            margin: 25px auto; background: #fff; padding: 25px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .form-control { width: 100%; padding: 12px; margin-bottom: 15px; border: 1px solid #cbd5e1;
            border-radius: 8px; box-sizing: border-box; background-color: #fff; font-size: 14px; font-family: inherit; }
        
        .field-explanation { font-size: 12px; color: #64748b; line-height: 1.5; margin-top: -10px; margin-bottom: 15px; padding: 0 4px; }

        button {
            width: 100%;
            padding: 14px; font-size: 16px; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; margin-top: 10px;
            transition: all 0.3s;
        }
        .btn-submit { background: #0078FF; color: white;
        }
        .btn-connect { background: #34A853; color: white;
        }
        .btn-confirm { background: #137333; color: white;
        }
        .btn-pay { background: #FF9900; color: white; font-size: 18px;
            text-shadow: 0 1px 2px rgba(0,0,0,0.2); }

        button:disabled {
            opacity: 0.85;
            cursor: not-allowed;
        }
        .btn-loading {
            background: #64748b !important;
        }

        .success-box { border: 2px solid #25D366; display: none;
        }
        .instructions-box { border: 2px solid #4285F4; display: none;
        }
        .confirmation-box { border: 2px solid #137333; display: none;
        }
        .error-box { border: 2px solid #ea4335; background: #fce8e6; display: none;
        }
    </style>
</head>
<body>

    <div id="preloader">
        <div class="loader-content">
            <div class="spinner"></div>
            <div class="loader-text">Cargando creador de asistentes...</div>
        </div>
    </div>

    <div id="flujo-principal">
        <h2>Elige la voz de tu asistente</h2>
        <p class="subtitle">Desliza a la derecha, escucha las muestras de audio y selecciona tu favorito</p>

        <div class="slider-wrapper-rel">
            <div class="slider-arrow left" onclick="desplazarSlider(-275)">❮</div>
            <div class="slider-arrow right" onclick="desplazarSlider(275)">❯</div>

            <div class="slider-container" id="voces-slider">
                <div class="card" data-voice="Cimo"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_aeaa8ab0b44f45d7a743cec6f4c52d71/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Cimo</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Brynne"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_8d7463b0d217475b854d4348b73225f5/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Brynne</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Chloe"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_6dee4ac9168044ca8d33ed61d2e1c82d/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Chloe</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Kate"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_3b57c0423f3946c09256f7a7da0f7e12/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Kate</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Grace"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_42cd8b8b69054fa48ee2f17a2fb14f07/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Grace</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Leland"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_56d5f8b0f9ea471194e84b6ca9dac329/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Leland</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Marissa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_41447efa9e474ae89bd52134a2f6e5fa/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Marissa</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Lily"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_2a8ec92d24a44bfe93ab905b79a2bbf8/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Lily</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Della"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_36d8c455c4a145618063be0974b049d9/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Della</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Nico"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_245713f3bfed47bb980ea4a93a5f96ea/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Nico</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Rita"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_1e7c8a61b2f4471cbf7a9508519e4d99/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Rita</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Meritt"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_292b27688f1548719b78bc144aca0083/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Meritt</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Willa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_0b4ec9d8dc8f417c9394be7d35740b90/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Willa</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Maren"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_4590f73dfb384b8f830bba9cc0102429/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Maren</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Tasmin"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_a2cf5a18d0544511b21f47de7f960267/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Tasmin</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Ashley"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_d75c49a573a94294a282f5f7c170731c/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Ashley</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Andrea"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_d058051cdf3045448cfcb9c06b5f49e3/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Andrea</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Claudia"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_03c20ace01274d4590fcfa22d79c310a/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Claudia</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Gaby"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_13c4419d5ee641e88733cf3d23157f4e/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Gaby</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Alejandro"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_86f94777baee49a1b2e30f486359fc56/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Alejandro</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Sloane"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_1bdc2297bbc74cf4801f939260e1e941/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Sloane</div><button class="btn-select" onclick="seleccionarAsistente(this)">Seleccionar</button></div></div>
            </div>
        </div>

        <div class="form-container">
            <h3>Datos del Negocio</h3>
            <input type="text" id="nombre_negocio" class="form-control" placeholder="Nombre del Negocio *">
            <input type="text" id="sector" class="form-control" placeholder="Sector / Tipo de Negocio *">
            <input type="text" id="servicios" class="form-control" placeholder="Productos y servicios *">
            <p class="field-explanation">Explica detalladamente todos los productos y servicios que ofreces. El asistente negará que tu negocio ofrezca cualquier cosa que no esté escrita explícitamente aquí, por lo que debes poner información completa.</p>
            <input type="text" id="horario" class="form-control" placeholder="Separación mínima ente dos citas que permitiremos reservar *">
            <input type="text" id="zona" class="form-control" placeholder="Zona de Servicio *">
            
            <select id="idioma" class="form-control">
                <option value="" disabled selected>Idioma del Asistente *</option>
                <option value="es">Español</option>
                <option value="en">Inglés</option>
            </select>

            <textarea id="informacion_cita" class="form-control" rows="2" placeholder="Información que pedirá el agente para reservar cita *"></textarea>
            
            <input type="email" id="google_calendar_email" class="form-control" placeholder="Email de tu Google Calendar *">

            <button id="btn-crear" onclick="procesarEnvio()" class="btn-submit">Crear Asistente Inteligente</button>
        </div>
    </div>

    <div id="success-box" class="success-box">
        <h3>¡Tu asistente está listo! 🎉</h3>
        <p><strong>Número asignado:</strong></p>
        <div id="telefono-display" style="font-size:32px; font-weight:bold; color:#25D366; margin:15px 0; text-align:center;"></div>
        <p style="color:#334155; line-height:1.5;">
            Este número tiene <strong>10 minutos gratuitos</strong> para que pruebes hablar con tu asistente.<br><br>
            Una vez realices el pago, se activará de forma permanente.
        </p>
        <button id="btn-conectar" onclick="mostrarInstrucciones()" class="btn-connect">🔗 Conectar con Google Calendar</button>
    </div>

    <div id="instructions-box" class="instructions-box">
        <h3>Cómo compartir tu calendario con Dansu</h3>
        <p><strong>Email que debes añadir:</strong></p>
        <p style="background:#f1f3f4; padding:12px; border-radius:8px; font-family:monospace; word-break:break-all; text-align:center; font-size:15px;">
            asistente-virtual@asistente-virtual-500413.iam.gserviceaccount.com
        </p>
        <ol style="text-align:left; line-height:1.8; font-size:15px;">
            <li>Abre <a href="https://calendar.google.com" target="_blank">Google Calendar</a></li>
            <li>En la parte izquierda, haz clic en <strong>"Mis calendarios"</strong></li>
            <li>Busca el calendario donde quieres recibir las citas, haz clic en los <strong>tres puntos ⋮</strong></li>
            <li>Selecciona <strong>"Configurar y compartir"</strong></li>
            <li>Baja hasta la sección <strong>"Compartido con"</strong></li>
            <li>Haz clic en <strong>"Añadir personas y grupos"</strong></li>
            <li>Pega el email de arriba</li>
            <li>Selecciona el permiso <strong>"Hacer cambios y gestionar el uso compartido"</strong></li>
            <li>Pulsa <strong>"Enviar"</strong></li>
        </ol>
        <button id="btn-ya-compartido" onclick="confirmarCompartido()" class="btn-confirm">✅ Ya he compartido el calendario</button>
    </div>

    <div id="confirmation-box" class="confirmation-box">
        <div id="success-message" style="display:none; text-align:center;">
            <p style="color:#137333; font-size:18px; font-weight:bold;">✅ Conexión verificada correctamente</p>
            <p style="margin:20px 0;">Ahora solo falta activar tu asistente de forma permanente.</p>
            <button onclick="ejecutarFlujoPago()" class="btn-pay">💳 Realizar Pago y Activar Permanentemente</button>
        </div>
        <div id="error-message" style="display:none;" class="error-box">
            <p><strong>❌ No se detectó el acceso</strong></p>
            <p>Revisa que hayas seguido todos los pasos y hayas dado los permisos correctos.</p>
            <button onclick="reintentarConexion()" style="background:#ea4335; color:white; width:100%; padding:12px; border:none; border-radius:8px;">Volver a intentarlo</button>
        </div>
    </div>

    <script>
        let asistenteSeleccionado = null;
        let currentCalendarEmail = "";

        // Función para mover el slider con las flechas
        function desplazarSlider(desplazamiento) {
            const slider = document.getElementById('voces-slider');
            slider.scrollBy({ left: desplazamiento, behavior: 'smooth' });
        }

        function seleccionarAsistente(button) {
            document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
            const card = button.closest('.card');
            card.classList.add('selected');
            asistenteSeleccionado = card.getAttribute('data-voice');
        }

        async function procesarEnvio() {
            const btn = document.getElementById('btn-crear');
            if (!asistenteSeleccionado) return alert("Por favor, selecciona una voz antes de continuar.");

            const payload = {
                asistente: asistenteSeleccionado,
                nombre_negocio: document.getElementById('nombre_negocio').value.trim(),
                sector: document.getElementById('sector').value.trim(),
                servicios: document.getElementById('servicios').value.trim(),
                horario: document.getElementById('horario').value.trim(),
                zona: document.getElementById('zona').value.trim(),
                idioma: document.getElementById('idioma').value,
                informacion_cita: document.getElementById('informacion_cita').value.trim(),
                google_calendar_email: document.getElementById('google_calendar_email').value.trim()
            };

            // Validación de que ningún campo esté vacío
            if (Object.values(payload).some(v => !v)) return alert("Completa todos los campos");
            // Estado de carga
            btn.disabled = true;
            btn.classList.add('btn-loading');
            btn.innerHTML = 'Creando asistente<span style="margin-left:8px;">⏳</span>';

            try {
                const res = await fetch("https://retell-bot.onrender.com/create-retell-bot", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                const data = await res.json();
                if (data.status === "success") {
                    currentCalendarEmail = payload.google_calendar_email;
                    document.getElementById('telefono-display').innerText = data.phone_number || "Número no asignado";
                    document.getElementById('success-box').style.display = 'block';
                    document.getElementById('success-box').scrollIntoView({ behavior: 'smooth' });
                } else {
                    alert("Error del servidor: " + (data.detail || "No se pudo crear"));
                }
            } catch (e) {
                alert("Error al crear el asistente");
            } finally {
                // Restaurar botón si falla
                btn.disabled = false;
                btn.classList.remove('btn-loading');
                btn.innerHTML = 'Crear Asistente Inteligente';
            }
        }

        function mostrarInstrucciones() {
            const btn = document.getElementById('btn-conectar');
            btn.disabled = true;
            btn.style.background = '#64748b';
            btn.innerHTML = 'Abriendo instrucciones...';

            document.getElementById('instructions-box').style.display = 'block';
            document.getElementById('instructions-box').scrollIntoView({ behavior: 'smooth' });
        }

        function reintentarConexion() {
            const btn = document.getElementById('btn-conectar');
            btn.disabled = false;
            btn.style.background = '#34A853';
            btn.innerHTML = '🔗 Conectar con Google Calendar';

            const btnCompartido = document.getElementById('btn-ya-compartido');
            btnCompartido.disabled = false;
            btnCompartido.style.background = '#137333';
            btnCompartido.innerHTML = '✅ Ya he compartido el calendario';

            document.getElementById('success-box').scrollIntoView({ behavior: 'smooth' });
        }

        async function confirmarCompartido() {
            const btn = document.getElementById('btn-ya-compartido');
            btn.disabled = true;
            btn.style.background = '#64748b';
            btn.innerHTML = 'Verificando acceso<span style="margin-left:8px;">⏳</span>';
            try {
                const res = await fetch("https://retell-bot.onrender.com/verify-calendar-access", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ calendar_email: currentCalendarEmail })
                });
                if (res.ok) {
                    document.getElementById('success-message').style.display = 'block';
                    document.getElementById('error-message').style.display = 'none';
                } else {
                    throw new Error();
                }
            } catch (err) {
                document.getElementById('success-message').style.display = 'none';
                document.getElementById('error-message').style.display = 'block';
            }

            document.getElementById('confirmation-box').style.display = 'block';
            document.getElementById('confirmation-box').scrollIntoView({ behavior: 'smooth' });
        }

        function ejecutarFlujoPago() {
            const nuevaVentana = window.open("about:blank", "_blank");
            if (!nuevaVentana || nuevaVentana.closed || typeof nuevaVentana.closed == 'undefined') {
                alert("Por favor, permite las ventanas emergentes para proceder al pago.");
                return;
            }

            nuevaVentana.document.write(`
                <html>
                <head>
                    <title>Cargando Pasarela de Pago...</title>
                    <style>
                        body {
                            font-family: 'Segoe UI', sans-serif;
                            display: flex; flex-direction: column; justify-content: center; align-items: center;
                            height: 100vh; margin: 0; background-color: #ffffff; color: #333;
                        }
                        .loader {
                            border: 4px solid #f3f3f3; border-top: 4px solid #0078FF;
                            border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite;
                            margin-bottom: 20px;
                        }
                        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
                    </style>
                </head>
                <body>
                    <div class="loader"></div>
                    <p>Redirigiendo de forma segura al paywall...</p>
                </body>
                </html>
            `);
            nuevaVentana.document.close();

            setTimeout(() => {
                if (nuevaVentana) nuevaVentana.location.href = "https://buy.stripe.com/dRm14n67u4L8aZhayo9AA00";
            }, 800);
        }

        // Preloader
        window.addEventListener('load', () => {
            const preloader = document.getElementById('preloader');
            setTimeout(() => {
                preloader.style.opacity = '0';
                setTimeout(() => preloader.style.display = 'none', 600);
            }, 700);
        });
    </script>
</body>
</html>
