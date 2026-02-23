# Используем официальный образ Python 3.11 (как у вас на Railway)
FROM python:3.11-slim

# Устанавливаем все необходимые системные C-библиотеки для WeasyPrint
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    python3-cffi \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Создаем папку для приложения
WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все остальные файлы бота (скрипты, шаблоны, картинки)
COPY . .

# Команда для запуска бота
CMD ["python", "bot_de.py"]