<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Área de Cliente - Dansu AI</title>
    <style>
        /* === PANTALLA DE CARGA / OVERLAY === */
        #loading-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(255,255,255,0.95);
            display: flex; flex-direction: column; align-items: center; justify-content: center; z-index: 99999;
            transition: opacity 0.4s ease;
        }
        .spinner {
            width: 70px; height: 70px; border: 6px solid #f3f3f3; border-top: 6px solid #0078FF;
            border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 20px;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        #loading-text { color: #334155; font-size: 15px; font-weight: 500; }

        /* Estilos e Identidad de creación */
        html, body {
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            margin: 0; padding: 10px; background: #eaf2f8; color: #333;
            overflow-y: auto; overflow-x: hidden;
        }
        h2, h3 { color: #111; margin-top: 0; }
        .subtitle { color: #666; font-size: 14px; margin-bottom: 20px; }

        .header-nav {
            max-width: 1000px; margin: 25px auto 20px auto; padding: 0 10px; text-align: center;
        }
        
        /* Contenedores Principales de estilo creación */
        .box-panel, .form-container {
            max-width: 500px;
            margin: 25px auto; background: #fff; padding: 25px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        
        /* Etiquetas de los campos */
        .form-label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: #475569;
            margin-bottom: 6px;
            padding: 0 2px;
        }

        .form-control { 
            width: 100%; padding: 12px; margin-bottom: 15px; border: 1px solid #cbd5e1;
            border-radius: 8px; box-sizing: border-box; background-color: #fff; font-size: 14px; font-family: inherit; resize: none; 
        }
        
        .field-explanation { font-size: 12px; color: #64748b; line-height: 1.5; margin-top: -10px; margin-bottom: 15px; padding: 0 4px; }

        /* Botones unificados */
        button {
            width: 100%; padding: 14px; font-size: 16px; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; margin-top: 10px;
            transition: all 0.3s;
        }
        .btn-submit { background: #0078FF; color: white; }
        .btn-submit:hover { background: #0066da; }
        .btn-secondary { background: #e2e8f0; color: #334155; }
        .btn-secondary:hover { background: #cbd5e1; }
        .btn-danger { background: #475569; color: white; margin-top: 25px; font-size: 14px; padding: 10px; }
        .btn-danger:hover { background: #334155; }

        button:disabled { opacity: 0.65; cursor: not-allowed; }
        .btn-loading { background: #64748b !important; }

        /* Mensajes de Alerta */
        .alert-box { padding: 12px; border-radius: 8px; font-size: 14px; margin-bottom: 15px; display: none; line-height: 1.4; text-align: left; }
        .alert-success { background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
        .alert-error { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }

        /* Grid de listado de asistentes */
        .grid-bots {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px; max-width: 1000px; margin: 0 auto 40px auto; padding: 0 10px;
        }
        .card-bot {
            background: white; border: 2px solid #e2e8f0; border-radius: 16px; padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); display: flex; flex-direction: column; justify-content: space-between;
        }
        .card-bot h4 { margin: 0 0 8px 0; font-size: 18px; color: #0f172a; }
        .card-bot p { margin: 0 0 15px 0; font-size: 14px; color: #64748b; line-height: 1.4; }
        
        .badge-phone {
            background: #f0fdf4; color: #16a34a; font-family: monospace; padding: 6px 10px;
            border-radius: 6px; font-size: 13px; font-weight: 600; display: inline-block; margin-bottom: 15px; border: 1px solid #bbf7d0;
        }

        /* === CARRUSEL DE VOCES === */
        .slider-wrapper-rel { position: relative; max-width: 100%; margin-bottom: 20px; text-align: left; }
        .slider-container {
            display: flex; overflow-x: auto; gap: 15px; padding: 10px 5px;
            scroll-snap-type: x mandatory; scrollbar-width: none; scroll-behavior: smooth;
        }
        .slider-container::-webkit-scrollbar { display: none; }
        .slider-arrow {
            position: absolute; top: 50%; transform: translateY(-50%); width: 40px; height: 40px;
            background: rgba(255, 255, 255, 0.9); border: 1px solid #e2e8f0; border-radius: 50%;
            display: flex; align-items: center; justify-content: center; font-size: 18px; cursor: pointer;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); z-index: 10; transition: all 0.2s ease; user-select: none;
        }
        .slider-arrow:hover { background: #fff; border-color: #0078FF; color: #0078FF; }
        .slider-arrow.left { left: -10px; }
        .slider-arrow.right { right: -10px; }
        @media (max-width: 600px) { .slider-arrow { display: none; } }

        .card { flex: 0 0 260px; border: 2px solid #e2e8f0; border-radius: 16px; background: #fff; scroll-snap-align: start; }
        .card.selected { border-color: #0078FF; box-shadow: 0 10px 15px rgba(0,120,255,0.2); }
        .video-wrapper { height: 160px; background: #000; border-top-left-radius: 14px; border-top-right-radius: 14px; overflow: hidden; }
        video { width: 100%; height: 100%; object-fit: cover; }
        .card-info { padding: 15px; text-align: center; }
        .card-name { font-size: 18px; font-weight: bold; margin-bottom: 12px; color: #111; }
        .btn-select { width: 100%; padding: 10px; border: 2px solid #0078FF; background: transparent; color: #0078FF; border-radius: 8px; font-weight: bold; cursor: pointer; margin-top: 0; }
        .card.selected .btn-select { background: #0078FF; color: white; }

        /* === MODAL DE ELIMINACIÓN ELEGANTE PERSONALIZADO === */
        .modal-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(4px); display: none; align-items: center; justify-content: center; z-index: 100000;
        }
        .modal-box {
            background: white; max-width: 420px; width: 90%; padding: 25px; border-radius: 16px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            text-align: center; animation: modalReveal 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes modalReveal { from { transform: scale(0.95); opacity: 0; } to { transform: scale(1); opacity: 1; } }
        .modal-box h4 { margin: 0 0 12px 0; font-size: 18px; color: #0f172a; }
        .modal-box p { font-size: 14px; color: #475569; line-height: 1.5; margin: 0 0 20px 0; }
        .modal-buttons { display: flex; gap: 12px; justify-content: center; }
        .modal-buttons button { margin-top: 0; padding: 12px; font-size: 14px; }
        .btn-modal-danger { background: #475569; color: white; }
        .btn-modal-danger:hover { background: #334155; }
        .btn-modal-success { background: #16a34a; color: white; }
        .btn-modal-success:hover { background: #15803d; }
    </style>
</head>
<body>

    <!-- Capa de Carga Síncrona -->
    <div id="loading-overlay">
        <div class="spinner"></div>
        <div id="loading-text">Verificando acceso...</div>
    </div>

    <!-- Barra Superior de Títulos -->
    <div class="header-nav">
        <h2 id="titulo-pantalla">Área de Clientes</h2>
        <div id="sub-pantalla" class="subtitle">Gestiona la configuración de tus asistentes virtuales</div>
    </div>

    <!-- CASO 1: Solicitud de Magic Link (Login) -->
    <div id="login-box" class="box-panel">
        <h3>Iniciar Sesión</h3>
        <div id="login-alert" class="alert-box"></div>
        <input type="email" id="login-email" class="form-control" placeholder="Tu correo de Google Calendar">
        <button id="btn-send-magic" class="btn-submit" onclick="enviarEnlaceMagico()">Enviar enlace de acceso ✨</button>
    </div>

    <!-- CASO 2: Listado General de Asistentes Activos -->
    <div id="panel-listado" style="display:none; max-width:1000px; margin:0 auto;">
        <div id="grid-asistentes" class="grid-bots"></div>
    </div>

    <!-- CASO 3: Formulario Avanzado de Edición -->
    <div id="panel-edicion" style="display:none; max-width:1000px; margin:0 auto; text-align:center;">
        
        <h3>Elige la voz de tu asistente</h3>
        <p class="subtitle">Desliza a la derecha, escucha las muestras de audio y selecciona tu favorito</p>

        <div class="slider-wrapper-rel">
            <div class="slider-arrow left" onclick="desplazarSlider(-275)">❮</div>
            <div class="slider-arrow right" onclick="desplazarSlider(275)">❯</div>

            <div class="slider-container" id="voces-slider">
                <div class="card" data-voice="Cimo"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_aeaa8ab0b44f45d7a743cec6f4c52d71/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Cimo</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Brynne"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_8d7463b0d217475b854d4348b73225f5/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Brynne</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Chloe"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_6dee4ac9168044ca8d33ed61d2e1c82d/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Chloe</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Kate"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_3b57c0423f3946c09256f7a7da0f7e12/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Kate</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Grace"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_42cd8b8b69054fa48ee2f17a2fb14f07/144p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Grace</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Leland"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_56d5f8b0f9ea471194e84b6ca9dac329/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Leland</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Marissa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_41447efa9e474ae89bd52134a2f6e5fa/480p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Marissa</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Lily"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_2a8ec92d24a44bfe93ab905b79a2bbf8/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Lily</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Della"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_36d8c455c4a145618063be0974b049d9/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Della</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Nico"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_245713f3bfed47bb980ea4a93a5f96ea/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Nico</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Rita"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_1e7c8a61b2f4471cbf7a9508519e4d99/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Rita</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Meritt"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_292b27688f1548719b78bc144aca0083/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Meritt</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Willa"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_0b4ec9d8dc8f417c9394be7d35740b90/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Willa</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Maren"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_4590f73dfb384b8f830bba9cc0102429/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Maren</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Tasmin"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_a2cf5a18d0544511b21f47de7f960267/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Tasmin</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Ashley"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_d75c49a573a94294a282f5f7c170731c/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Ashley</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Andrea"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_d058051cdf3045448cfcb9c06b5f49e3/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Andrea</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Claudia"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_03c20ace01274d4590fcfa22d79c310a/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Claudia</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Gaby"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_13c4419d5ee641e88733cf3d23157f4e/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Gaby</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Alejandro"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_86f94777baee49a1b2e30f486359fc56/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Alejandro</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
                <div class="card" data-voice="Sloane"><div class="video-wrapper"><video controls preload="metadata" playsinline><source src="https://video.wixstatic.com/video/a23405_1bdc2297bbc74cf4801f939260e1e941/720p/mp4/file.mp4" type="video/mp4"></video></div><div class="card-info"><div class="card-name">Sloane</div><button type="button" class="btn-select" onclick="seleccionarAsistenteEdicion(this)">Seleccionar</button></div></div>
            </div>
        </div>

        <div class="form-container" style="text-align: left; margin-top: 10px;">
            <h3>Datos del Negocio</h3>
            <div id="edit-alert" class="alert-box"></div>

            <label class="form-label" for="edit-nombre">Nombre del Negocio</label>
            <input type="text" id="edit-nombre" class="form-control" placeholder="Nombre del Negocio">
            
            <label class="form-label" for="edit-sector">Sector / Tipo de Negocio</label>
            <input type="text" id="edit-sector" class="form-control" placeholder="Sector / Tipo de Negocio">
            
            <label class="form-label" for="edit-servicios">Productos y servicios</label>
            <input type="text" id="edit-servicios" class="form-control" placeholder="Productos y servicios">
            <p class="field-explanation">Cuantos más detalles des, más detalles dará el asistente.</p>
            
            <label class="form-label" for="edit-horario">Horario de apertura</label>
            <input type="text" id="edit-horario" class="form-control" placeholder="Horario de apertura">
            
            <label class="form-label" for="edit-duracion-cita-select">Tiempo de separación mínimo entre dos citas</label>
            <select id="edit-duracion-cita-select" class="form-control"></select>
            
            <label class="form-label" for="edit-zona">Zona de Servicio</label>
            <input type="text" id="edit-zona" class="form-control" placeholder="Zona de Servicio">
            
            <label class="form-label" for="edit-idioma">Idioma del Asistente</label>
            <select id="edit-idioma" class="form-control">
                <option value="es">Español</option>
                <option value="en">Inglés</option>
            </select>

            <label class="form-label" for="edit-datos-reserva">Información que se pedirá al cliente para reservar cita</label>
            <textarea id="edit-datos-reserva" class="form-control" rows="2" placeholder="Información que se pedirá al cliente para reservar cita"></textarea>
            
            <label class="form-label" for="edit-calendar">Email de tu Google Calendar</label>
            <input type="email" id="edit-calendar" class="form-control" placeholder="Email de tu Google Calendar" disabled>
            <p class="field-explanation">Después podrás conectar con tu CRM a través de tu calendario de Google.</p>

            <button id="btn-guardar-cambios" class="btn-submit" onclick="guardarCambiosAsistente()">Guardar y Sincronizar Cambios</button>
            <button type="button" class="btn-secondary" onclick="ejecutarAccionRegresar()">Cancelar</button>

            <button type="button" class="btn-danger" onclick="abrirModalConfirmacionEliminar()">Eliminar Asistente Permanentemente</button>
        </div>
    </div>

    <!-- ESTRUCTURA DEL DIÁLOGO EMERGENTE DE CONFIRMACIÓN/ÉXITO -->
    <div id="custom-delete-modal" class="modal-overlay">
        <div class="modal-box">
            <h4 id="modal-title">¿Eliminar Asistente?</h4>
            <p id="modal-message">¿Estás completamente seguro de que deseas eliminar permanentemente este asistente virtual? Esta acción no se puede deshacer.</p>
            <div id="modal-actions" class="modal-buttons">
                <button type="button" class="btn-secondary" onclick="cerrarModalEliminar()">Cancelar</button>
                <button type="button" class="btn-modal-danger" id="btn-confirm-delete-action">Eliminar de todos modos</button>
            </div>
        </div>
    </div>

    <script>
        const BACKEND_URL = "https://retell-bot.onrender.com";
        let usuarioEmail = "";
        let listaBots = [];
        let botEnEdicion = null;
        let vozSeleccionadaEdicion = null;
        let estadoNavegacionActual = "login"; // "login", "listado", "edicion"

        const INVERSE_VOICE_MAPPING = {
            "11labs-Adrian": "Cimo", "11labs-Brynne": "Brynne", "11labs-Chloe": "Chloe",
            "openai-Nova": "Kate", "openai-Shimmer": "Grace", "11labs-Leland": "Leland",
            "11labs-Marissa": "Marissa", "11labs-Lily": "Lily", "11labs-Delia": "Della",
            "openai-Onyx": "Nico", "11labs-Rita": "Rita", "11labs-Meritt": "Meritt",
            "11labs-Willa": "Willa", "11labs-Maren": "Maren", "11labs-Tasmin": "Tasmin",
            "11labs-Ashley": "Ashley", "openai-Alloy": "Andrea", "11labs-Claudia": "Claudia",
            "11labs-Gaby": "Gaby", "openai-Echo": "Alejandro", "11labs-Sloane": "Sloane"
        };

        const VOICE_MAPPING_SIMULADO = {
            "Cimo": "11labs-Adrian", "Brynne": "11labs-Brynne", "Chloe": "11labs-Chloe",
            "Kate": "openai-Nova", "Grace": "openai-Shimmer", "Leland": "11labs-Leland",
            "Marissa": "11labs-Marissa", "Lily": "11labs-Lily", "Della": "11labs-Delia",
            "Nico": "openai-Onyx", "Rita": "11labs-Rita", "Meritt": "11labs-Meritt",
            "Willa": "11labs-Willa", "Maren": "11labs-Maren", "Tasmin": "11labs-Tasmin",
            "Ashley": "11labs-Ashley", "Andrea": "openai-Alloy", "Claudia": "11labs-Claudia",
            "Gaby": "11labs-Gaby", "Alejandro": "openai-Echo", "Sloane": "11labs-Sloane"
        };

        // GENERACIÓN DINÁMICA DEL DESPLEGABLE EN EDICIÓN
        window.addEventListener('DOMContentLoaded', () => {
            const selectDuracion = document.getElementById('edit-duracion-cita-select');
            for (let minutos = 15; minutos <= 480; minutos += 15) {
                const opt = document.createElement('option');
                opt.value = minutos;
                if (minutos < 60) {
                    opt.text = `${minutos} minutos`;
                } else {
                    const horas = Math.floor(minutos / 60);
                    const minsRestantes = minutos % 60;
                    let textoHora = horas === 1 ? "1 hora" : `${horas} horas`;
                    let textoMinutos = minsRestantes > 0 ? ` y ${minsRestantes} min` : "";
                    opt.text = `${textoHora}${textoMinutos}`;
                }
                selectDuracion.appendChild(opt);
            }
            verificarSesion();
        });

        window.addEventListener('popstate', (event) => {
            if (estadoNavegacionActual === "edicion") {
                irAListado();
                history.pushState({view: "listado"}, "");
            } else if (estadoNavegacionActual === "listado") {
                history.pushState({view: "listado"}, "");
            }
        });

        function desplazarSlider(desplazamiento) {
            const slider = document.getElementById('voces-slider');
            slider.scrollBy({ left: desplazamiento, behavior: 'smooth' });
        }

        function seleccionarAsistenteEdicion(button) {
            document.querySelectorAll('#panel-edicion .card').forEach(c => c.classList.remove('selected'));
            const card = button.closest('.card');
            card.classList.add('selected');
            vozSeleccionadaEdicion = card.getAttribute('data-voice');
        }

        async function verificarSesion() {
            mostrarLoading("Verificando credenciales de acceso...");
            try {
                const res = await fetch(`${BACKEND_URL}/check-session`);
                const data = await res.json();
                if (data.status === "success" && data.bots) {
                    usuarioEmail = data.email;
                    listaBots = data.bots;
                    ocultarLoading();
                    irAListado();
                } else {
                    ocultarLoading();
                    irALogin();
                }
            } catch (err) {
                ocultarLoading();
                irALogin();
            }
        }

        function mostrarLoading(texto) {
            document.getElementById('loading-text').innerText = texto;
            document.getElementById('loading-overlay').style.display = 'flex';
        }
        function ocultarLoading() {
            document.getElementById('loading-overlay').style.display = 'none';
        }

        function irALogin() {
            estadoNavegacionActual = "login";
            document.getElementById('titulo-pantalla').style.display = 'block';
            document.getElementById('titulo-pantalla').innerText = "Área de Clientes";
            document.getElementById('sub-pantalla').style.display = 'block';
            document.getElementById('sub-pantalla').innerText = "Introduce tu correo para acceder de forma segura";
            document.getElementById('panel-listado').style.display = 'none';
            document.getElementById('panel-edicion').style.display = 'none';
            document.getElementById('login-box').style.display = 'block';
        }

        function irAListado() {
            estadoNavegacionActual = "listado";
            document.getElementById('titulo-pantalla').style.display = 'block';
            document.getElementById('titulo-pantalla').innerText = "Mis Asistentes AI";
            document.getElementById('sub-pantalla').style.display = 'block';
            document.getElementById('sub-pantalla').innerText = `Panel de gestión de agentes para: ${usuarioEmail}`;
            document.getElementById('login-box').style.display = 'none';
            document.getElementById('panel-edicion').style.display = 'none';
            document.getElementById('panel-listado').style.display = 'block';
            renderizarTarjetas();
        }

        function ejecutarAccionRegresar() {
            document.querySelectorAll('#panel-edicion video').forEach(v => v.pause());
            irAListado();
        }

        function irAEdicion(agentId) {
            botEnEdicion = listaBots.find(b => b.agent_id === agentId);
            if (!botEnEdicion) return;

            estadoNavegacionActual = "edicion";
            history.pushState({view: "edicion"}, "");

            document.getElementById('titulo-pantalla').style.display = 'none';
            document.getElementById('sub-pantalla').style.display = 'none';

            document.getElementById('edit-nombre').value = botEnEdicion.nombre_negocio || "";
            document.getElementById('edit-sector').value = botEnEdicion.sector || "";
            document.getElementById('edit-servicios').value = botEnEdicion.servicios || "";
            document.getElementById('edit-horario').value = botEnEdicion.horario || "";
            
            const duracionDB = botEnEdicion.duracion_cita || "30";
            const selectDuracion = document.getElementById('edit-duracion-cita-select');
            selectDuracion.value = duracionDB;
            if (!selectDuracion.value) selectDuracion.value = "30";
            
            document.getElementById('edit-zona').value = botEnEdicion.zona || "";
            document.getElementById('edit-idioma').value = botEnEdicion.idioma || "es";
            document.getElementById('edit-datos-reserva').value = botEnEdicion.datos_reserva || "";
            document.getElementById('edit-calendar').value = botEnEdicion.google_calendar_email || "";

            document.querySelectorAll('#panel-edicion .card').forEach(c => c.classList.remove('selected'));
            const vozActualLegible = INVERSE_VOICE_MAPPING[botEnEdicion.asistente] || botEnEdicion.asistente || 'Andrea';
            
            const tarjetaVozActual = document.querySelector(`#panel-edicion .card[data-voice="${vozActualLegible}"]`);
            if (tarjetaVozActual) {
                tarjetaVozActual.classList.add('selected');
                vozSeleccionadaEdicion = vozActualLegible;
                setTimeout(() => {
                    tarjetaVozActual.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                }, 100);
            } else {
                vozSeleccionadaEdicion = null;
            }

            document.getElementById('edit-alert').style.display = 'none';
            document.getElementById('login-box').style.display = 'none';
            document.getElementById('panel-listado').style.display = 'none';
            document.getElementById('panel-edicion').style.display = 'block';
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function renderizarTarjetas() {
            const contenedor = document.getElementById('grid-asistentes');
            contenedor.innerHTML = "";

            if (listaBots.length === 0) {
                contenedor.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <p style="margin: 0; color: #64748b; font-weight: 500;">No se encontraron asistentes virtuales creados con este correo.</p>
                    </div>
                `;
                return;
            }

            listaBots.forEach(bot => {
                const card = document.createElement('div');
                card.className = "card-bot";
                card.innerHTML = `
                    <div>
                        <h4>${bot.nombre_negocio || 'Asistente Dansu'}</h4>
                        <p style="font-size:12px; font-weight:600; text-transform:uppercase; color:#0078FF; letter-spacing:0.5px; margin-bottom:8px;">${bot.sector || 'AI Agent'}</p>
                        <div class="badge-phone">${bot.phone_number || 'Número pendiente'}</div>
                        <p><strong>Servicios:</strong> ${bot.servicios ? bot.servicios.substring(0, 80) + '...' : 'No definidos'}</p>
                    </div>
                    <button class="btn-secondary" style="padding:10px; font-size:14px; margin-top:10px;" onclick="irAEdicion('${bot.agent_id}')">⚙️ Gestionar Configuración</button>
                `;
                contenedor.appendChild(card);
            });
        }

        async function guardarCambiosAsistente() {
            if (!botEnEdicion) return;
            if (!vozSeleccionadaEdicion) return alert("Por favor, selecciona una voz operativa antes de guardar.");
            
            const alertBox = document.getElementById('edit-alert');
            const btn = document.getElementById('btn-guardar-cambios');

            const payload = {
                agent_id: botEnEdicion.agent_id,
                nombre_negocio: document.getElementById('edit-nombre').value.trim(),
                sector: document.getElementById('edit-sector').value.trim(),
                servicios: document.getElementById('edit-servicios').value.trim(),
                horario: document.getElementById('edit-horario').value.trim(),
                duracion_cita: document.getElementById('edit-duracion-cita-select').value,
                zona: document.getElementById('edit-zona').value.trim(),
                idioma: document.getElementById('edit-idioma').value,
                datos_reserva: document.getElementById('edit-datos-reserva').value.trim(),
                asistente: VOICE_MAPPING_SIMULADO[vozSeleccionadaEdicion],
                google_calendar_email: botEnEdicion.google_calendar_email
            };

            if (!payload.nombre_negocio || !payload.sector || !payload.servicios || !payload.horario || !payload.zona || !payload.datos_reserva) {
                alertBox.className = "alert-box alert-error";
                alertBox.innerText = "Todos los campos operativos de la configuración son obligatorios.";
                alertBox.style.display = "block";
                return;
            }

            btn.disabled = true;
            btn.classList.add('btn-loading');
            btn.innerText = "Sincronizando cambios en la nube... ⏳";
            alertBox.style.display = "none";

            try {
                const res = await fetch(`${BACKEND_URL}/update-retell-bot`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.status === "success") {
                    alertBox.className = "alert-box alert-success";
                    alertBox.innerText = "🚀 ¡Sincronizado! Los cambios se han inyectado en el agente de voz correctamente.";
                    alertBox.style.display = "block";
                    const idx = listaBots.findIndex(b => b.agent_id === botEnEdicion.agent_id);
                    if (idx !== -1) listaBots[idx] = { ...listaBots[idx], ...payload };
                    document.querySelectorAll('#panel-edicion video').forEach(v => v.pause());
                    setTimeout(() => irAListado(), 1500);
                } else {
                    throw new Error(data.detail || "Error al actualizar");
                }
            } catch (err) {
                alertBox.className = "alert-box alert-error";
                alertBox.innerText = "Error al intentar actualizar la configuración del agente en Retell AI.";
                alertBox.style.display = "block";
            } finally {
                btn.disabled = false;
                btn.classList.remove('btn-loading');
                btn.innerText = "Guardar y Sincronizar Cambios";
            }
        }

        /* === NUEVA LÓGICA DE ELIMINACIÓN PREMIUM CON MODAL INTEGRADO === */
        function abrirModalConfirmacionEliminar() {
            if (!botEnEdicion) return;
            
            const modal = document.getElementById('custom-delete-modal');
            const title = document.getElementById('modal-title');
            const message = document.getElementById('modal-message');
            const actions = document.getElementById('modal-actions');
            
            // Configurar Modal en modo Pregunta Inicial
            title.innerText = "¿Eliminar Asistente?";
            title.style.color = "#0f172a";
            message.innerText = `¿Estás completamente seguro de que deseas eliminar permanentemente el asistente de "${botEnEdicion.nombre_negocio || 'tu negocio'}"? Esta acción es irreversible.`;
            
            actions.innerHTML = `
                <button type="button" class="btn-secondary" onclick="cerrarModalEliminar()">Cancelar</button>
                <button type="button" class="btn-modal-danger" id="btn-confirm-delete-action" onclick="ejecutarEliminacionServidor()">Eliminar de todos modos</button>
            `;
            
            modal.style.display = "flex";
        }

        function cerrarModalEliminar() {
            document.getElementById('custom-delete-modal').style.display = "none";
        }

        async function ejecutarEliminacionServidor() {
            const btnConfirmar = document.getElementById('btn-confirm-delete-action');
            if (btnConfirmar) {
                btnConfirmar.disabled = true;
                btnConfirmar.innerText = "Eliminando... ⏳";
            }

            try {
                // Detener posibles audios/videos antes de ocultar
                document.querySelectorAll('#panel-edicion video').forEach(v => v.pause());

                const res = await fetch(`${BACKEND_URL}/delete-retell-bot`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ agent_id: botEnEdicion.agent_id })
                });
                const data = await res.json();

                if (data.status === "success") {
                    // Actualizar el array local excluyendo al bot eliminado
                    listaBots = listaBots.filter(b => b.agent_id !== botEnEdicion.agent_id);
                    
                    // Transformar el Modal en vista de éxito educada y elegante
                    const title = document.getElementById('modal-title');
                    const message = document.getElementById('modal-message');
                    const actions = document.getElementById('modal-actions');

                    title.innerText = "Asistente eliminado correctamente ✨";
                    title.style.color = "#16a34a";
                    message.innerHTML = "El asistente se ha eliminado de forma educada del sistema.<br><br>Queremos informarte de que las suscripciones asociadas a este agente **se dejarán de cobrar en tu cuenta inmediatamente después de la facturación del mes en curso**.";
                    
                    actions.innerHTML = `
                        <button type="button" class="btn-modal-success" onclick="finalizarFlujoEliminacion()">Entendido, volver al panel</button>
                    `;
                } else {
                    alert("Error en el servidor al intentar eliminar. Por favor, inténtalo de nuevo.");
                    cerrarModalEliminar();
                }
            } catch (error) {
                alert("Error de conexión con el servidor de Dansu.");
                cerrarModalEliminar();
            }
        }

        function finalizarFlujoEliminacion() {
            cerrarModalEliminar();
            document.getElementById('panel-edicion').style.display = 'none';
            irAListado();
        }

        function eliminarAsistenteDesdeEdicion() {
            abrirModalConfirmacionEliminar();
        }

        async function enviarEnlaceMagico() {
            const emailInput = document.getElementById('login-email').value.trim();
            const alertBox = document.getElementById('login-alert');
            const btn = document.getElementById('btn-send-magic');

            if (!emailInput) {
                alertBox.className = "alert-box alert-error";
                alertBox.innerText = "Por favor, introduce una dirección de correo válida.";
                alertBox.style.display = "block";
                return;
            }

            btn.disabled = true;
            btn.innerText = "Enviando enlace... ⏳";
            alertBox.style.display = "none";

            try {
                const res = await fetch(`${BACKEND_URL}/request-magic-link`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: emailInput })
                });
                const data = await res.json();

                if (data.status === "success") {
                    alertBox.className = "alert-box alert-success";
                    alertBox.innerText = "🔑 Te hemos enviado un correo con tu enlace mágico de acceso. Revisa tu bandeja de entrada y spam.";
                    alertBox.style.display = "block";
                    btn.innerText = "¡Enlace Enviado! 👍";
                } else {
                    throw new Error(data.detail || "Error interno");
                }
            } catch (err) {
                alertBox.className = "alert-box alert-error";
                alertBox.innerText = "No se pudo enviar el acceso en este momento. Verifica tu email.";
                alertBox.style.display = "block";
                btn.disabled = false;
                btn.innerText = "Enviar enlace de acceso ✨";
            }
        }
    </script>
</body>
</html>
