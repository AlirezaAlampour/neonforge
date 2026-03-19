FROM nvcr.io/nvidia/pytorch:24.10-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="${SERVICE}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install required system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 git tini \
    && rm -rf /var/lib/apt/lists/*

# --- THE FIX ---
# Clone the repo into /opt so it doesn't get overwritten by /app bind mounts
WORKDIR /opt
RUN git clone https://github.com/KwaiVGI/LivePortrait.git

# Tell Python to look inside the cloned repository for modules
ENV PYTHONPATH="/opt/LivePortrait:$PYTHONPATH"
# ---------------

# Now switch back to /app for your normal application files
WORKDIR /app
COPY requirements.txt .

# (Keep the rest of your Dockerfile the exact same from here down)

# Strip out the CPU PyTorch downloads so we keep the pre-installed NVIDIA GPU version
RUN sed -i '/^torch/d' requirements.txt && \
    sed -i '/^--extra-index-url/d' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt || pip install --no-cache-dir --break-system-packages -r requirements.txt

# --- ADD THIS SECTION ---
# Clone the LivePortrait repository into the current working directory (/app)
# We move the contents of the repo up one level so the 'liveportrait' folder 
# is directly accessible as a Python module to app.py.
RUN git clone https://github.com/KwaiVGI/LivePortrait.git temp_repo && \
    mv temp_repo/* . && \
    rm -rf temp_repo
# ------------------------

COPY app.py .
EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]