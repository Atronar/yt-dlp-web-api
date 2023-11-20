FROM docker.io/python:3
RUN apt update
RUN apt install -y ffmpeg gifsicle
RUN mkdir /workspace
RUN mkdir /workspace/downloads
ADD requirements.txt /workspace/
WORKDIR /workspace
RUN pip3 install -r requirements.txt
RUN pip3 install --upgrade sentry-sdk
ADD run.py /workspace
ADD .conf.json /workspace
