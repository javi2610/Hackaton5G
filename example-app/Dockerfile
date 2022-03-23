FROM ubuntu:18.04 as builder
ARG INSTALL_DIR=/opt/intel/openvino_2021
ARG VERSION=2021.3.394
RUN apt update
RUN apt install -y gnupg2 curl sudo
RUN curl  https://apt.repos.intel.com/openvino/2021/GPG-PUB-KEY-INTEL-OPENVINO-2021 | apt-key add -
RUN echo 'deb https://apt.repos.intel.com/openvino/2021 all main' > /etc/apt/sources.list.d/intel-openvino.list
RUN apt update
RUN apt install -y --no-install-recommends \
	intel-openvino-ie-rt-hddl-ubuntu-bionic-$VERSION \
	intel-openvino-ie-samples-$VERSION \
	intel-openvino-setupvars-$VERSION \
	intel-openvino-omz-dev-$VERSION \
	intel-openvino-omz-tools-$VERSION \
	intel-openvino-model-optimizer-$VERSION \
	intel-openvino-ie-rt-cpu-ubuntu-bionic-$VERSION \
	intel-openvino-opencv-etc-$VERSION \
	intel-openvino-opencv-generic-$VERSION \
	intel-openvino-opencv-lib-ubuntu-bionic-$VERSION \
	intel-openvino-pot-$VERSION \
    intel-openvino-runtime-ubuntu18-$VERSION

RUN echo "yes" | $INSTALL_DIR/install_dependencies/install_openvino_dependencies.sh
RUN mkdir /root/openvino_2021 && mkdir /root/openvino_2021/lib && mkdir /root/openvino_2021/python
RUN  cp -r /opt/intel/openvino_2021/deployment_tools/inference_engine/lib/intel64/* /root/openvino_2021/lib/ && \
     cp /lib/x86_64-linux-gnu/libusb-1.0.so.0 /root/openvino_2021/lib/ && \
     cp -r $INSTALL_DIR/python /root/openvino_2021 && \
     cp -r $INSTALL_DIR/opencv/lib /root/openvino_2021 && \
     cp -r /opt/intel/openvino_2021/deployment_tools/inference_engine/external/hddl /root && \
     /bin/bash -c "source /opt/intel/openvino_2021/bin/setupvars.sh && \
     ldd /opt/intel/openvino_2021/deployment_tools/inference_engine/lib/intel64/libHDDLPlugin.so" | grep opt | awk '{print $3}' | xargs -Iaaa cp aaa /root/openvino_2021/lib/

FROM ubuntu:18.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-setuptools \
    libpython3.6 \
    libjson-c3 \
    libboost-filesystem1.65 \
    libboost-thread1.65 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN apt-get update && \
    apt-get install \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libxvidcore-dev \
    libx264-dev \
    libgtk-3-dev \
    ffmpeg -y


COPY --from=builder /root/ /root/

ADD app /home/openvino/text_recognition

ADD app/openvino_models /home/openvino/text_recognition/openvino_models
ADD app/app.py app/requirements.txt /home/openvino/text_recognition/

WORKDIR /home/openvino/text_recognition/

USER root
RUN chmod -R +777 .

RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt

ENV HDDL_INSTALL_DIR=/root/hddl
ENV LD_LIBRARY_PATH=/root/openvino/lib/
ENV PYTHONPATH=/root/openvino/python/python3.6:/root/openvino/python/python3