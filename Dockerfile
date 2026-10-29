FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Set working directory
WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HEADLESS_MODE=true
ENV HOST=0.0.0.0

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and static assets
COPY src/ ./src/
COPY static/ ./static/
COPY run.py .
COPY export_session.py .
COPY README.md .
COPY minglebot_session.zip* ./

# Create runtime directories
RUN mkdir -p /app/data /app/browser_data

# Start MingleBot application
CMD ["python", "run.py"]
