<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Área de Cliente - Dansu AI</title>
    <style>
        html, body {
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            margin: 0; padding: 15px; background: transparent; color: #333;
            /* Solución a la doble barra de scroll en integraciones tipo Wix/Iframe */
            overflow-y: auto;
            overflow-x: hidden;
        }
        
        /* Cabecera con Navegación */
        .header-nav {
            display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px;
        }
        h2 { color: #111; margin: 0; }
        .subtitle { color: #666; font-size: 14px; margin-bottom: 25px; }

        /* Botón Volver a Inicio */
        .btn-back-home {
            display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px;
            background: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; border-radius: 10px;
            font-weight: 600; font-size: 13px; text-decoration: none; cursor: pointer; transition: all 0.2s;
        }
        .btn-back-home:hover { background: #e2e8f0; color: #0f172a; }
        
        /* Caja de Autenticación Inicial */
        .login-box {
            max-width: 400px; margin: 40px auto; padding: 30px;
            background: #fff; border: 1px solid #e2e8f0; border-radius: 20px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.05); text-align: center;
        }
        .login-box input {
            width: 100%; padding: 12px; margin: 15px 0; border: 1px solid #cbd5e1;
            border-radius: 12px; box-sizing: border-box; font-size: 14px; text-align: center;
        }
        .btn-primary {
            width: 100%; padding: 12px; background: #0078FF; color: white;
            border: none; border-radius: 12px; font-weight: 600; cursor: pointer; transition: background 0.2s;
        }
        .btn-primary:hover { background: #0056B3; }

        /* Grid de Asistentes */
        .panel-container { display: none; }
        .bots-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px; margin-bottom: 30px;
        }
        .bot-card {
            border: 1px solid #e2e8f0; border-radius: 16px; background: #fff; padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); position: relative;
        }
        .bot-title { font-size: 18px; font-weight: 600; color: #111; margin: 0 0 5px 0; }
        .bot-sector { 
            font-size: 11px; font-weight: 600; text-transform: uppercase; color: #0078FF; 
            background: #E6F2FF; padding: 3px 8px; border-radius: 20px; display: inline-block; margin-bottom: 15px;
        }
        .bot-info { font-size: 13px; color: #555; margin-bottom: 8px; }
        .bot-info strong { color: #222; }
        
        .action-buttons-container { display: flex; gap: 8px; margin-top: 15px; }
        .btn-manage {
            flex: 2; padding: 10px; background: #0078FF; color: #fff;
            border: none; border-radius: 10px; font-weight: 600; cursor: pointer;
        }
        .btn-manage:hover { background: #0056B3; }
        
        .btn-delete-bot {
            flex: 1; padding: 10px; background: #ef4444; color: #fff;
            border: none; border-radius: 10px; font-weight: 600; cursor: pointer;
        }
        .btn-delete-bot:hover { background: #b91c1c; }

        /* Formulario de Edición */
        .edit-box {
            display: none; background: #fff; border: 1px solid #e2e8f0; border-radius: 20px;
            padding: 25px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.1); margin-top: 20px;
        }
        .form-group { margin-bottom: 15px; }
        label { display: block; font-weight: 600; font-size: 13px; margin-bottom: 6px; color: #444; }
        input[type="text"], input[type="email"], textarea {
            width: 100%; padding: 11px; border: 1px solid #cbd5e1; border-radius: 10px; box-sizing: border-box; font-family: inherit; font-size: 14px;
        }
        textarea { resize: vertical; height: 80px; }

        /* Carrusel de Voces en Edición */
        .slider-container {
            display: flex; overflow-x: auto; gap: 15px; padding: 10px 5px;
            scroll-snap-type: x mandatory; scrollbar-width: none; margin-bottom: 20px;
        }
        .slider-container::-webkit-scrollbar { display: none; }
        .card { flex: 0 0 240px; border: 2px solid #e2e8f0; border-radius: 16px; background: #fff; scroll-snap-align: start; }
        .card.selected { border-color: #0078FF; box-shadow: 0 10px 15px rgba(0,120,255,0.15); }
        .video-wrapper { height: 130px; background: #000; border-top-left-radius: 14px; border-top-right-radius: 14px; overflow: hidden; }
        video { width: 100%; height: 100%; object-fit: cover; }
        .card-info { padding: 12px; text-align: center; }
        .card-name { font-size: 16px; font-weight: bold; margin-bottom: 8px; }
        .btn-select { width: 100%; padding: 8px; border: 2px solid #0078FF; background: transparent; color: #0078FF; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 13px; }
        .card.selected .btn-select { background: #0078FF; color: white; }
        
        .btn-container { display: flex; gap: 10px; margin-top: 20px; }
        .btn-save { flex: 2; padding: 12px; background: #10B981; color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; }
        .btn-save:hover { background: #059669; }
        .btn-cancel { flex: 1; padding: 12px 20px; background: #64748b; color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; }
        .btn-cancel:hover { background: #475569; }

        .btn-danger-zone {
            width: 100%; padding: 12px; background: #ef4444; color: white; border: none; border-radius: 10px; font-weight: 600; cursor: pointer; margin-top: 15px; transition: background 0.2s;
        }
        .btn-danger-zone:hover { background: #b91c1c; }
        
        .no-bots { text-align: center; color: #94a3b8; padding: 40px 0; font-size: 15px; grid-column: 1 / -1; }
    </style>
</head>
<body>

    <div class="header-nav">
        <a href="https://dansu.info" target="_top" class="btn-back-home">
            ← Volver a Inicio
        </a>
    </div>

    <div id="auth-screen" class="login-box">
        <h3 style="margin-top:0;">Accede a tu Panel</h3>
        <p id="auth-instruction" style="font-size:13px; color:#666; margin:0;">Introduce el Email de Google Calendar vinculado a tus asistentes para gestionarlos de forma segura.</p>
        <input type="email" id="auth-email" placeholder="ejemplo@gmail.com" required>
        <button id="btn-login" class="btn-primary" onclick="solicitarEnlaceMagico()">Recibir Enlace Mágico ✨</button>
        <p id="manual-trigger-text" style="font-size:11px; margin-top:15px; color:#94a3b8; cursor:pointer;" onclick="conmutarModoManual()">¿Prefieres entrar de manera manual tradicional?</p>
    </div>

    <div id="main-panel" class="panel-container">
        <h2>Tus Asistentes Inteligentes</h2>
        <p class="subtitle">Gestiona, reconfigura y actualiza el comportamiento operativo de tus agentes en tiempo real.</p>
        <div id="bots-container" class="bots-grid"></div>

        <div id="edit-panel" class="edit-box">
            <h3 style="margin-top: 0; color:#111;">⚙️ Editar Parámetros del Asistente</h3>
            
            <div class="form-group">
                <label>Voz del Asistente Virtual</label>
                <div class="slider-container">
                    <div class="card" data-voice="Cimo"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_aeaa8ab0b44f45d7a743cec6f4c52d71/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Cimo</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Brynne"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_8d7463b0d217475b854d4348b73225f5/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Brynne</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Chloe"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_6dee4ac9168044ca8d33ed61d2e1c82d/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Chloe</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Kate"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_3b57c0423f3946c09256f7a7da0f7e12/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Kate</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Grace"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_42cd8b8b69054fa48ee2f17a2fb14f07/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Grace</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Leland"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_56d5f8b0f9ea471194e84b6ca9dac329/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Leland</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Marissa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_41447efa9e474ae89bd52134a2f6e5fa/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Marissa</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Lily"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_2a8ec92d24a44bfe93ab905b79a2bbf8/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Lily</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Della"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_36d8c455c4a145618063be0974b049d9/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Della</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Nico"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_3959c8fa727040bfbe03f00996bc5bf9/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Nico</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Rita"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_0cb800885f9e4e6fb2478f79fbc9b7b9/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Rita</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Meritt"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_d994fa4cfc88421c977df76fbf431b9c/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Meritt</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Willa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_bfb69cdb90ec422197be07c8702b87ff/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Willa</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Maren"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_4a317769e5bd478fb1f56860d5bfa780/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Maren</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Tasmin"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_1cbfa8fb96cd4b5fae563065b7cb3ca4/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Tasmin</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Ashley"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_df7179069d2f45fb88b39b56f8f10665/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Ashley</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Andrea"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_b66b0393f9e94bf6be76e33ce1d5a3ec/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Andrea</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Claudia"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_df2881b76dfc4066af8dbfb5be16ba48/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Claudia</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Gaby"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_8f5ba2f89f2d4e1ca91307b22a9477b8/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Gaby</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Alejandro"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_fc8890cb5b3d4a23bda783c139c878f2/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Alejandro</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                    <div class="card" data-voice="Sloane"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_41a384fec030467794cc4d63b27b3726/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Sloane</div><button type="button" class="btn-select" onclick="seleccionarVozEdicion(this)">Seleccionar</button></div></div>
                </div>
            </div>

            <div class="form-group">
                <label for="edit-nombre">Nombre del Negocio</label>
                <input type="text" id="edit-nombre" required>
            </div>
            <div class="form-group">
                <label for="edit-sector">Sector de Actividad</label>
                <input type="text" id="edit-sector" required>
            </div>
            <div class="form-group">
                <label for="edit-servicios">Servicios Disponibles (Separa por comas)</label>
                <textarea id="edit-servicios" required></textarea>
            </div>
            <div class="form-group">
                <label for="edit-horario">Horario Comercial</label>
                <input type="text" id="edit-horario" required>
            </div>
            <div class="form-group">
                <label for="edit-zona">Zona de Ubicación</label>
                <input type="text" id="edit-zona" required>
            </div>
            <div class="form-group">
                <label for="edit-calendar">Email de Google Calendar (Donde se agendará)</label>
                <input type="email" id="edit-calendar" required>
            </div>

            <div class="btn-container">
                <button class="btn-save" onclick="guardarCambios()">Guardar Configuración 💾</button>
                <button class="btn-cancel" onclick="cerrarEdicion()">Cancelar</button>
            </div>

            <button class="btn-danger-zone" id="btn-eliminar-global" onclick="ejecutarEliminacionDesdeForm()">
                ⚠️ Eliminar este Asistente Permanentemente
            </button>
        </div>
    </div>

    <script>
        const BACKEND_URL = "https://retell-bot.onrender.com";
        let emailUsuario = "";
        let listaBots = [];
        let botEnEdicion = null;
        let vozSeleccionadaNombre = "";
        let modoManualActivo = false;

        // ==================== COMPROBACIÓN INVISIBLE POR ASOCIACIÓN DE IP ====================
        window.onload = async function() {
            console.log("🚀 Iniciando comprobación de sesión segura...");
            document.getElementById('auth-instruction').innerText = "⏳ Sincronizando credenciales de acceso con el servidor...";
            document.getElementById('auth-email').style.display = 'none';
            document.getElementById('btn-login').style.display = 'none';
            document.getElementById('manual-trigger-text').style.display = 'none';
            
            try {
                const res = await fetch(`${BACKEND_URL}/check-session`);
                const data = await res.json();

                if (res.ok && data.status === "success") {
                    emailUsuario = data.email;
                    listaBots = data.bots;
                    document.getElementById('auth-screen').style.display = 'none';
                    document.getElementById('main-panel').style.display = 'block';
                    renderizarTarjetas();
                } else {
                    console.log("No hay sesión por IP activa.");
                    restaurarPantallaLogin();
                }
            } catch (error) {
                console.error("Error en autologin por IP:", error);
                restaurarPantallaLogin();
            }
        };

        function restaurarPantallaLogin() {
            modoManualActivo = false;
            document.getElementById('auth-instruction').innerText = "Introduce el Email de Google Calendar vinculado a tus asistentes para gestionarlos de forma segura.";
            document.getElementById('auth-email').style.display = 'block';
            document.getElementById('auth-email').value = "";
            document.getElementById('btn-login').style.display = 'block';
            document.getElementById('btn-login').className = "btn-primary";
            document.getElementById('btn-login').innerText = "Recibir Enlace Mágico ✨";
            document.getElementById('btn-login').setAttribute("onclick", "solicitarEnlaceMagico()");
            document.getElementById('btn-login').disabled = false;
            document.getElementById('manual-trigger-text').style.display = 'block';
            document.getElementById('manual-trigger-text').innerText = "¿Prefieres entrar de manera manual tradicional?";
        }

        function conmutarModoManual() {
            if (!modoManualActivo) {
                modoManualActivo = true;
                document.getElementById('auth-instruction').innerText = "Introduce tu email para cargar tus asistentes manualmente (Modo de contingencia).";
                document.getElementById('btn-login').innerText = "Ver mis Asistentes";
                document.getElementById('btn-login').setAttribute("onclick", "cargarAsistentes()");
                document.getElementById('manual-trigger-text').innerText = "← Volver al acceso seguro por Enlace Mágico";
            } else {
                restaurarPantallaLogin();
            }
        }

        async function solicitarEnlaceMagico() {
            const emailInput = document.getElementById('auth-email').value.trim();
            if (!emailInput || !emailInput.includes("@")) {
                return alert("Por favor, introduce un correo electrónico válido.");
            }
            
            const btn = document.getElementById('btn-login');
            btn.innerText = "Enviando enlace...";
            btn.disabled = true;

            try {
                const res = await fetch(`${BACKEND_URL}/request-magic-link`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: emailInput })
                });
                const data = await res.json();
                
                if (res.ok && data.status === "success") {
                    alert("¡Enlace enviado! Revisa tu correo y haz clic en él para entrar directamente.");
                    btn.innerText = "¡Enviado! Revisa tu email";
                } else {
                    alert("Aviso del Servidor: " + (data.detail || "No se pudo enviar."));
                    btn.innerText = "Recibir Enlace Mágico ✨";
                    btn.disabled = false;
                }
            } catch (error) {
                alert("Error de conexión al solicitar el enlace.");
                btn.innerText = "Recibir Enlace Mágico ✨";
                btn.disabled = false;
            }
        }

        // ==================== FUNCIONES ORIGINALES (CONSERVADAS AL 100%) ====================
        async function cargarAsistentes() {
            const emailInput = document.getElementById('auth-email').value.trim();
            if (!emailInput) {
                return alert("Por favor, introduce un correo electrónico válido.");
            }

            emailUsuario = emailInput;

            try {
                const res = await fetch(`${BACKEND_URL}/get-asistentes`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ calendar_email: emailUsuario })
                });

                if (!res.ok) throw new Error("Error en la respuesta del servidor");

                listaBots = await res.json();
                document.getElementById('auth-screen').style.display = 'none';
                document.getElementById('main-panel').style.display = 'block';
                renderizarTarjetas();

            } catch (error) {
                alert("No se pudieron cargar los asistentes. Revisa la conexión con el backend.");
            }
        }

        function renderizarTarjetas() {
            const container = document.getElementById("bots-container");
            container.innerHTML = "";

            if (!listaBots || listaBots.length === 0) {
                container.innerHTML = `<div class="no-bots">No se encontraron asistentes virtuales configurados para ${emailUsuario}.</div>`;
                return;
            }

            listaBots.forEach(bot => {
                const card = document.createElement("div");
                card.className = "bot-card";
                card.innerHTML = `
                    <div class="bot-title">${bot.nombre_negocio || 'Asistente Sin Nombre'}</div>
                    <div class="bot-sector">${bot.sector || 'General'}</div>
                    <div class="bot-info"><strong>ID de Retell:</strong> ${bot.agent_id}</div>
                    <div class="bot-info"><strong>Teléfono:</strong> ${bot.phone_number || 'No asignado'}</div>
                    <div class="bot-info"><strong>Voz Actual:</strong> ${bot.asistente || 'Default'}</div>
                    <div class="bot-info"><strong>Horario:</strong> ${bot.horario}</div>
                    <div class="bot-info"><strong>Ubicación:</strong> ${bot.zona}</div>
                    <div class="action-buttons-container">
                        <button class="btn-manage" onclick='abrirEdicion(${JSON.stringify(bot)})'>Configurar Parámetros ⚙️</button>
                        <button class="btn-delete-bot" onclick="eliminarAsistente('${bot.agent_id}', '${bot.nombre_negocio}')">🗑️</button>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function seleccionarVozEdicion(buttonElement) {
            const container = buttonElement.closest('.slider-container');
            container.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
            
            const card = buttonElement.closest('.card');
            card.classList.add('selected');
            vozSeleccionadaNombre = card.getAttribute('data-voice');
        }

        document.addEventListener("DOMContentLoaded", () => {
             const videos = document.querySelectorAll(".video-wrapper video");
             videos.forEach(v => {
                 v.addEventListener("play", () => {
                     videos.forEach(otherVideo => {
                         if (otherVideo !== v) otherVideo.pause();
                     });
                 });
             });
        });

        function abrirEdicion(bot) {
            botEnEdicion = bot;
            vozSeleccionadaNombre = bot.asistente || "";

            document.getElementById("edit-panel").style.display = "block";
            document.getElementById("edit-nombre").value = bot.nombre_negocio || "";
            document.getElementById("edit-sector").value = bot.sector || "";
            document.getElementById("edit-servicios").value = bot.servicios || "";
            document.getElementById("edit-horario").value = bot.horario || "";
            document.getElementById("edit-zona").value = bot.zona || "";
            document.getElementById("edit-calendar").value = bot.google_calendar_email || "";

            const slider = document.querySelector(".slider-container");
            slider.querySelectorAll('.card').forEach(c => {
                c.classList.remove('selected');
                if (c.getAttribute('data-voice') === vozSeleccionadaNombre) {
                    c.classList.add('selected');
                    c.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                }
            });

            document.getElementById("edit-panel").scrollIntoView({ behavior: "smooth" });
        }

        function cerrarEdicion() {
            document.getElementById("edit-panel").style.display = "none";
            botEnEdicion = null;
        }

        async function guardarCambios() {
            if (!botEnEdicion) return;

            const fields = {
                agent_id: botEnEdicion.agent_id,
                nombre_negocio: document.getElementById("edit-nombre").value.trim(),
                sector: document.getElementById("edit-sector").value.trim(),
                servicios: document.getElementById("edit-servicios").value.trim(),
                horario: document.getElementById("edit-horario").value.trim(),
                zona: document.getElementById("edit-zona").value.trim(),
                google_calendar_email: document.getElementById("edit-calendar").value.trim(),
                asistente: vozSeleccionadaNombre
            };

            if (Object.values(fields).some(val => !val)) {
                return alert("Por favor, rellena todos los campos operacionales y selecciona una voz.");
            }

            try {
                const res = await fetch(`${BACKEND_URL}/update-asistente`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(fields)
                });

                const data = await res.json();

                if (data.status === "success" && data.bot) {
                    alert("¡Asistente reconfigurado y actualizado con éxito en la IA!");
                    listaBots = listaBots.map(b => b.agent_id === fields.agent_id ? data.bot : b);
                    cerrarEdicion();
                    renderizarTarjetas();
                } else {
                    alert("Error al actualizar: " + (data.detail || "Verifica los datos."));
                }
            } catch (e) {
                alert("Error de red al intentar guardar los datos del asistente.");
            }
        }

        function ejecutarEliminacionDesdeForm() {
            if (!botEnEdicion) return;
            eliminarAsistente(botEnEdicion.agent_id, botEnEdicion.nombre_negocio || 'este asistente');
        }

        async function eliminarAsistente(agentId, nombreBot) {
            const confirmacion = confirm(`¿Estás completamente seguro de que deseas eliminar permanentemente a "${nombreBot}"?\nEsta acción es irreversible, dará de baja el bot en Retell AI y liberará su número telefónico.`);
            if (!confirmacion) return;

            try {
                if (botEnEdicion && botEnEdicion.agent_id === agentId) {
                    cerrarEdicion();
                }

                const res = await fetch(`${BACKEND_URL}/delete-asistente`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ agent_id: agentId })
                });

                const data = await res.json();

                if (data.status === "success") {
                    alert("El asistente ha sido eliminado correctamente de todos los sistemas.");
                    listaBots = listaBots.filter(b => b.agent_id !== agentId);
                    renderizarTarjetas();
                } else {
                    alert("Error al intentar eliminar el bot: " + (data.detail || "Inténtalo de nuevo."));
                }
            } catch (error) {
                alert("Error de red al intentar comunicarse con el servidor para la eliminación.");
            }
        }
    </script>
</body>
</html>
