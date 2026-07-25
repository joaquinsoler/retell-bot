const express = require('express');
const cors = require('cors');
const { Nango } = require('@nangohq/node');
const { Pool } = require('pg');

const app = express();
app.use(cors());
app.use(express.json());

// ========== NANGO ==========
const nango = new Nango({ 
  secretKey: process.env.NANGO_API_KEY 
});

// ========== BASE DE DATOS (Render Postgres) ==========
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

// Crear tabla automáticamente al arrancar
async function initDB() {
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS nango_connections (
        id SERIAL PRIMARY KEY,
        connection_id VARCHAR(255) UNIQUE NOT NULL,
        provider_config_key VARCHAR(255),
        provider VARCHAR(100),
        tags JSONB,
        created_at TIMESTAMP DEFAULT NOW()
      );
    `);
    console.log('✅ Tabla nango_connections lista');
  } catch (err) {
    console.error('❌ Error creando tabla:', err.message);
  }
}
initDB();

// ========== ENDPOINT PARA EL FRONTEND ==========
// El frontend llama a este endpoint para obtener el sessionToken
app.post('/session-token', async (req, res) => {
  try {
    const { data } = await nango.createConnectSession({
      allowed_integrations: ['google'],
      tags: {
        end_user_id: req.body.userId || 'user-' + Date.now(),
        end_user_email: req.body.email || null
      }
    });

    res.json({ sessionToken: data.token });
  } catch (err) {
    console.error('Error creando session token:', err.message);
    res.status(500).json({ error: err.message });
  }
});

// ========== WEBHOOK DE NANGO ==========
// Aquí se recibe la confirmación de autenticación y se guarda en la DB
app.post('/nango-webhook', async (req, res) => {
  const payload = req.body;

  console.log('🔔 Webhook recibido de Nango:');
  console.log(JSON.stringify(payload, null, 2));

  if (payload.type === 'auth' && payload.operation === 'creation' && payload.success === true) {
    try {
      await pool.query(
        `INSERT INTO nango_connections 
         (connection_id, provider_config_key, provider, tags)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (connection_id) DO NOTHING`,
        [
          payload.connectionId,
          payload.providerConfigKey,
          payload.provider,
          JSON.stringify(payload.tags || {})
        ]
      );

      console.log(`✅ Autenticación Google guardada → connectionId: ${payload.connectionId}`);
    } catch (err) {
      console.error('❌ Error guardando en la base de datos:', err.message);
    }
  }

  res.status(200).send('OK');
});

// Health check
app.get('/', (req, res) => {
  res.send('Servidor Nango + Google OAuth funcionando correctamente');
});

// Arrancar servidor
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`🚀 Servidor corriendo en puerto ${PORT}`);
});
