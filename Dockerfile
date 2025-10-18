# FROM mcr.microsoft.com/playwright/python:v1.37.0-jammy
FROM python:3.11-slim

# Install dependencies untuk WeasyPrint
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 \
    curl \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Run bot
CMD ["python", "bot.py"]
