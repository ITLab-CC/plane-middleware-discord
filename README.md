# Plane Middleware for Discord

1. Create an `.env` file in this directory with the following content:
   ```
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/lxhfdsiknfviuhfnkshnrz498t7n98t7zh4w08rtzw304n7zt0b87nt08s7zutb8e47hmtr8erbtn8entb
    PLANE_BASE_URL=https://plane.example.com
    PLANE_API_TOKEN=plane_api_replacemewithanapitockenbyplane
   ```
2. Create a venv, install the requirements and run the middleware:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    python3 main.py
    ```

# Docker
To run the middleware in a Docker container, you can use the provided `docker-compose.yml` file.