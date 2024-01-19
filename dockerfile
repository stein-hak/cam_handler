FROM ubuntu:bionic
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpulse0 \
    pulseaudio \
    python3-mutagen \
    python3-gi \
    python3-gi-cairo \
    python3-dbus \
    python3-dev \
    gir1.2-gtk-3.0 \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gir1.2-gst-rtsp-server-1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-pulseaudio \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    gstreamer1.0-rtsp \
    gstreamer1.0-opencv \
    gstreamer1.0-tools \
    opencv-data \
    libxml2 \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    zlib1g-dev \
    libzbar0 \
    cmake \
    gcc \
    g++ \
    git \
    python3-redis \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    python3-lxml \
    iputils-ping \
    bash \
    ffmpeg

RUN pip3 install onvif_zeep face_recognition pyzbar
ENV PYTHONUNBUFFERED=1
WORKDIR /opt/cam_handler
COPY . .
RUN chmod +x /opt/cam_handler/cam_handler.py
CMD /opt/cam_handler/cam_handler.py
EXPOSE 8888

