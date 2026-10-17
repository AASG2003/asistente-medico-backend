# Asistente médico: backend

API REST que convierte notas en texto libre sobre hábitos, síntomas y estados en datos estructurados usando **Gemma**, el modelo abierto de Google.

> "Dormí 5 horas, tomé café y me duele la cabeza desde la tarde"

| Concepto | Tipo | Valor | Momento |
|---|---|---|---|
| sueno | habit | 5 hours | |
| cafe | habit | *pregunta: ¿cuántas tazas?* | |
| dolor de cabeza | symptom | *pregunta: ¿del 0 al 10?* | tarde |

Si falta una cantidad o la nota es ambigua, la API devuelve **preguntas de seguimiento** y completa la entrada con las respuestas.

> Proyecto educativo. No es un dispositivo médico ni reemplaza la consulta profesional.

## Tecnologías

- Python 3.14 (cualquier versión 3.14.x excepto 3.14.1)
- Django 5.2 LTS y Django REST Framework 3.17
- drf-spectacular (documentación Swagger)
- google-genai (Gemma vía Gemini API)
- SQLite por defecto; PostgreSQL cambiando una variable

## Instalación

```bash
git clone https://github.com/AASG2003/asistente-medico-backend.git
cd asistente-medico-backend
```

Crear y activar el entorno virtual:

```bash
# Windows
py -3.14 -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3.14 -m venv .venv
source .venv/bin/activate
```

Instalar dependencias y configurar:

```bash
pip install -r requirements.txt
cp .env.example .env        # en Windows: copy .env.example .env
python manage.py migrate
python verify.py            # revisa que todo esté listo
```

## Configuración (`.env`)

| Variable | Descripción |
|---|---|
| `LLM_PROVIDER` | `fake` funciona sin internet ni API key; `gemma` usa el modelo real |
| `GEMINI_API_KEY` | Clave gratuita de [Google AI Studio](https://aistudio.google.com/apikey) |
| `LLM_MODEL` | Modelo a usar (por defecto `gemma-4-26b-a4b-it`) |
| `LLM_THINKING_LEVEL` | `minimal` (rápido, ~5 s) o `high` (más lento) |
| `DATABASE_URL` | Vacío usa SQLite; ver [PostgreSQL](#postgresql) |
| `TIME_ZONE` | Zona horaria, por defecto `America/La_Paz` |

## Uso

```bash
python manage.py runserver
```
