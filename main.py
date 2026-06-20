<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Creador de Asistentes AI - Dansu</title>
    <style>
        html, body {
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            margin: 0; padding: 10px; background: transparent; color: #333;
        }
        h2, h3 { color: #111; }
        .subtitle { color: #666; font-size: 14px; margin-bottom: 20px; }

        .slider-container {
            display: flex; overflow-x: auto; gap: 15px; padding: 10px 5px;
            scroll-snap-type: x mandatory; scrollbar-width: none;
        }
        .slider-container::-webkit-scrollbar { display: none; }

        .card { flex: 0 0 260px; border: 2px solid #e2e8f0; border-radius: 16px; background: #fff; }
        .card.selected { border-color: #0078FF; box-shadow: 0 10px 15px rgba(0,120,255,0.2); }

        .video-wrapper { height: 160px; background: #000; }
        video { width: 100%; height: 100%; object-fit: cover; }

        .card-info { padding: 15px; }
        .card-name { font-size: 18px; font-weight: bold; margin-bottom: 12px; }

        .btn-select { width: 100%; padding: 10px; border: 2px solid #0078FF; background: transparent; color: #0078FF; border-radius: 8px; font-weight: bold; cursor: pointer; }
        .card.selected .btn-select { background: #0078FF; color: white; }

        .form-container, .success-box, .instructions-box, .confirmation-box {
            max-width: 500px; margin: 25px auto; background: #fff; padding: 25px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .form-control { width: 100%; padding: 12px; margin-bottom: 15px; border: 1px solid #cbd5e1; border-radius: 8px; box-sizing: border-box; }

        button { width: 100%; padding: 14px; font-size: 16px; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; margin-top: 10px; }
        .btn-submit { background: #0078FF; color: white; }
        .btn-connect { background: #34A853; color: white; }
        .btn-confirm { background: #137333; color: white; }
        .btn-pagar { background: #EA4335; color: white; font-size: 18px; }

        .success-box { border: 2px solid #25D366; display: none; }
        .instructions-box { border: 2px solid #4285F4; display: none; }
        .confirmation-box { display: none; }
        .error-box { border: 2px solid #ea4335; background: #fce8e6; display: none; }
    </style>
</head>
<body>

    <div id="flujo-principal">
        <h2>Elige la voz de tu asistente</h2>
        <p class="subtitle">Desliza a la derecha, escucha las muestras de audio y selecciona tu favorito</p>

        <div class="slider-container">
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

        <div class="form-container">
            <h3>Datos del Negocio</h3>
            <input type="text" id="nombre_negocio" class="form-control" placeholder="Nombre del Negocio *">
            <input type="text" id="sector" class="form-control" placeholder="Sector / Tipo de Negocio *">
            <input type="text" id="servicios" class="form-control" placeholder="Servicios que ofreces *">
            <input type="text" id="horario" class="form-control" placeholder="Horario Comercial *">
            <input type="text" id="zona" class="form-control" placeholder="Zona de Servicio *">
            <input type="email" id="google_calendar_email" class="form-control" placeholder="Email de tu Google Calendar *">

            <button onclick="procesarEnvio()" class="btn-submit">Crear Asistente Inteligente ✨</button>
        </div>
    </div>

    <div id="success-box" class="success-box">
        <h3>¡Tu asistente está listo! 🎉</h3>
        <p><strong>Teléfono de prueba:</strong></p>
        <div id="telefono-display" style="font-size:28px; font-weight:bold; color:#25D366; margin:15px 0;"></div>
        
        <p style="background:#f0fdf4; padding:15px; border-radius:8px; border-left:4px solid #25D366; margin:20px 0;">
            ✅ Se ha activado tu <strong>prueba gratuita de 10 minutos</strong>. 
            ¡Llama ahora al número de arriba y prueba tu asistente inteligente!
        </p>
        
        <button onclick="mostrarInstrucciones()" class="btn-connect">🔗 Conectar con Google Calendar</button>
    </div>

    <div id="instructions-box" class="instructions-box">
        <h3>Cómo compartir tu calendario con Dansu</h3>
        <p><strong>Email que debes usar:</strong></p>
        <p style="background:#f1f3f4; padding:12px; border-radius:8px; font-family:monospace; word-break:break-all;">
            dansu-voice-assistant@dansu-technologies.iam.gserviceaccount.com
        </p>
        <ol style="text-align:left; line-height:1.7;">
            <li>Abre <a href="https://calendar.google.com" target="_blank">Google Calendar</a></li>
            <li>Haz clic en los 3 puntos del calendario principal → "Configuración y uso compartido"</li>
            <li>En "Compartir con personas específicas" → "Añadir personas"</li>
            <li>Pega el email de arriba</li>
            <li>Selecciona <strong>"Hacer cambios y gestionar el uso compartido"</strong></li>
            <li>Pulsa "Enviar"</li>
        </ol>
        <button onclick="confirmarCompartido()" class="btn-confirm">✅ Ya he compartido el calendario</button>
    </div>

    <div id="confirmation-box" class="confirmation-box">
        <div id="success-message" style="display:none;">
            <p style="color:#137333; font-size:18px; font-weight:bold;">✅ Conexión verificada correctamente</p>
            
            <a id="btn-probar-llamada" href="#" style="display:block; padding:14px; background:#25D366; color:white; text-align:center; text-decoration:none; border-radius:8px; margin-top:10px;">
                📞 Hacer llamada de prueba
            </a>

            <!-- BOTÓN DE PAGAR AÑADIDO -->
            <a href="CONFIRMACION_EXITO" style="display:block; padding:16px; background:#EA4335; color:white; text-align:center; text-decoration:none; border-radius:8px; margin-top:15px; font-size:18px; font-weight:bold;">
                💳 PAGAR Y ACTIVAR ASISTENTE
            </a>
        </div>

        <div id="error-message" style="display:none;" class="error-box">
            <p><strong>❌ No se detectó el acceso</strong></p>
            <p>Revisa que hayas compartido el calendario principal y que los permisos estén correctos.</p>
            <button onclick="mostrarInstrucciones()" style="background:#ea4335; color:white;">Intentar de nuevo</button>
        </div>
    </div>

    <script>
        let asistenteSeleccionado = null;
        let currentCalendarEmail = "";

        function seleccionarAsistente(button) {
            document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
            button.closest('.card').classList.add('selected');
            asistenteSeleccionado = button.closest('.card').getAttribute('data-voice');
        }

        async function procesarEnvio() {
            if (!asistenteSeleccionado) return alert("Selecciona una voz");

            const payload = {
                asistente: asistenteSeleccionado,
                nombre_negocio: document.getElementById('nombre_negocio').value.trim(),
                sector: document.getElementById('sector').value.trim(),
                servicios: document.getElementById('servicios').value.trim(),
                horario: document.getElementById('horario').value.trim(),
                zona: document.getElementById('zona').value.trim(),
                google_calendar_email: document.getElementById('google_calendar_email').value.trim()
            };

            if (Object.values(payload).some(v => !v)) return alert("Completa todos los campos");

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
                    
                    if(data.phone_number) {
                        const btnLlamada = document.getElementById('btn-probar-llamada');
                        btnLlamada.href = `tel:${data.phone_number}`;
                    }
                } else {
                    alert("Error del servidor: " + (data.detail || "No se pudo crear"));
                }
            } catch (e) {
                alert("Error al crear el asistente");
            }
        }

        function mostrarInstrucciones() {
            document.getElementById('instructions-box').style.display = 'block';
            document.getElementById('instructions-box').scrollIntoView({ behavior: 'smooth' });
        }

        async function confirmarCompartido() {
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
            // Scroll automático hacia el final para que el botón "Pagar" sea visible
            document.getElementById('confirmation-box').scrollIntoView({ behavior: 'smooth' });
        }
    </script>
</body>
</html>
